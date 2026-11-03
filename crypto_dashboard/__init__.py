import hmac
import math
import os
import secrets
from collections import defaultdict
from datetime import date as calendar_date
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for

from .coinbase import CoinbaseClient, CoinbaseError
from .database import (
    cache_price,
    cache_prices,
    cached_price_is_fresh,
    get_cached_price,
    get_account_balances,
    get_connection,
    initialize_database,
    migrate_market_price_cache,
    read_setting,
    set_setting,
    store_account_balances,
    store_transactions,
)
from .ledger import (
    CATEGORIES, FIAT_CURRENCIES, _eur_value, build_dashboard, canonical_asset,
    decimal_value, default_category, normalized_transaction,
)
from .market import (
    BinanceMarketData,
    CoinbaseMarketData,
    CoinGeckoMarketData,
    FallbackMarketData,
    MarketDataError,
)
from .quality import build_market_quality_issues, summarize_quality_issues


def register_template_filters(app):
    @app.template_filter("money")
    def money(value):
        if value is None:
            return "—"
        try:
            rendered = f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",")
            return f"{rendered} €"
        except (InvalidOperation, ValueError, TypeError):
            return "—"

    @app.template_filter("quantity")
    def quantity(value):
        if value is None:
            return "—"
        try:
            rendered = f"{Decimal(str(value)):,.8f}".rstrip("0").rstrip(".")
            return rendered.replace(",", " ").replace(".", ",")
        except (InvalidOperation, ValueError, TypeError):
            return "—"

    @app.template_filter("percent")
    def percent(value):
        if value is None:
            return "—"
        try:
            return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",") + "%"
        except (InvalidOperation, ValueError, TypeError):
            return "—"

    @app.template_filter("category")
    def category(transaction_type):
        return default_category(transaction_type)


def parse_transaction_filters(args):
    allowed_sorts = {
        "created_at", "type", "amount_currency", "amount", "native_amount", "status", "category"
    }
    try:
        page = max(1, int(args.get("page", "1")))
        per_page = int(args.get("per_page", "25"))
    except ValueError:
        abort(400, "Pagination invalide.")
    if per_page not in {25, 50, 100}:
        abort(400, "Taille de page invalide.")
    sort = args.get("sort", "created_at")
    if sort not in allowed_sorts:
        abort(400, "Tri invalide.")
    direction = args.get("direction", "desc").lower()
    if direction not in {"asc", "desc"}:
        abort(400, "Sens de tri invalide.")
    category = args.get("category", "")
    if category and category not in CATEGORIES:
        abort(400, "Catégorie invalide.")
    dates = {}
    for key in ("from_date", "to_date"):
        value = args.get(key, "")
        if value:
            try:
                dates[key] = calendar_date.fromisoformat(value).isoformat()
            except ValueError:
                abort(400, "Date de filtre invalide.")
        else:
            dates[key] = ""
    if dates["from_date"] and dates["to_date"] and dates["from_date"] > dates["to_date"]:
        abort(400, "La date de début doit précéder la date de fin.")
    return {
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "direction": direction,
        "q": args.get("q", "").strip()[:100],
        "asset": args.get("asset", "").strip().upper()[:20],
        "category": category,
        "status": args.get("status", "").strip()[:30],
        **dates,
    }


def query_transactions(connection, filters):
    category_sql = (
        "COALESCE(c.category, CASE "
        "WHEN t.type IN ('staking_transfer', 'unstaking_transfer', 'retail_eth2_deprecation', "
        "'pro_deposit', "
        "'pro_withdrawal', 'intx_deposit', 'intx_withdrawal', 'wrap_asset', 'unwrap_asset') "
        "AND NOT (t.amount_currency IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') "
        "AND t.type IN ('pro_deposit', 'pro_withdrawal')) THEN 'Transfer' "
        "WHEN t.type IN ('earn_payout', 'staking_reward', 'interest', "
        "'incentives_rewards_payout', 'subscription_rebate') THEN 'DIVIDEND' "
        "WHEN t.type = 'buy' THEN 'BUY' WHEN t.type = 'sell' THEN 'SELL' "
        "WHEN t.type = 'fiat_deposit' THEN 'Deposit' "
        "WHEN t.type = 'fiat_withdrawal' THEN 'Withdrawal' "
        "WHEN t.type = 'receive' THEN 'Deposit' "
        "WHEN t.type = 'send' AND CAST(t.amount AS REAL) > 0 THEN 'Deposit' "
        "WHEN t.type = 'send' THEN 'Withdrawal' "
        "WHEN t.type = 'pro_withdrawal' THEN 'Withdrawal' "
        "WHEN t.type = 'pro_deposit' THEN 'Deposit' "
        "WHEN t.type = 'advanced_trade_fill' AND CAST(t.amount AS REAL) > 0 "
        "AND t.amount_currency NOT IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'BUY' "
        "WHEN t.type = 'advanced_trade_fill' AND CAST(t.amount AS REAL) < 0 "
        "AND t.amount_currency NOT IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'SELL' "
        "WHEN COALESCE(json_extract(t.raw_json, '$.advanced_trade_fill.order_side'), "
        "json_extract(t.raw_json, '$.order_side')) = 'buy' "
        "AND t.type = 'advanced_trade_fill' "
        "AND t.amount_currency IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'BUY' "
        "WHEN COALESCE(json_extract(t.raw_json, '$.advanced_trade_fill.order_side'), "
        "json_extract(t.raw_json, '$.order_side')) = 'sell' "
        "AND t.type = 'advanced_trade_fill' "
        "AND t.amount_currency IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'SELL' "
        "WHEN COALESCE(json_extract(t.raw_json, '$.advanced_trade_fill.order_side'), "
        "json_extract(t.raw_json, '$.order_side')) = 'buy' "
        "AND t.amount_currency NOT IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'BUY' "
        "WHEN COALESCE(json_extract(t.raw_json, '$.advanced_trade_fill.order_side'), "
        "json_extract(t.raw_json, '$.order_side')) = 'sell' "
        "AND t.amount_currency NOT IN ('EUR', 'USD', 'GBP', 'CHF', 'CAD', 'AUD', 'JPY', "
        "'NZD', 'SGD', 'MXN', 'BRL', 'ZAR', 'SEK', 'NOK', 'DKK', 'PLN', 'TRY', 'EURC') THEN 'SELL' "
        "WHEN t.type IN ('trade', 'tx') AND CAST(t.amount AS REAL) > 0 THEN 'BUY' "
        "WHEN t.type IN ('trade', 'tx') THEN 'SELL' "
        "WHEN t.type = 'retail_simple_dust' THEN 'SELL' "
        "ELSE 'Other' END)"
    )
    clauses = []
    values = []
    if filters["q"]:
        clauses.append("(t.id LIKE ? OR t.description LIKE ? OR t.type LIKE ? OR t.amount_currency LIKE ?)")
        pattern = f'%{filters["q"]}%'
        values.extend([pattern] * 4)
    if filters["asset"]:
        clauses.append("UPPER(t.amount_currency) = ?")
        values.append(filters["asset"])
    if filters["category"]:
        clauses.append(f"{category_sql} = ?")
        values.append(filters["category"])
    if filters["status"]:
        clauses.append("t.status = ?")
        values.append(filters["status"])
    if filters["from_date"]:
        clauses.append("t.created_at >= ?")
        values.append(filters["from_date"])
    if filters["to_date"]:
        clauses.append("t.created_at < ?")
        next_day = calendar_date.fromisoformat(filters["to_date"]) + timedelta(days=1)
        values.append(f"{next_day.isoformat()}T00:00:00")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total = connection.execute(
        f"SELECT COUNT(*) FROM transactions t LEFT JOIN categories c "
        f"ON c.transaction_id = t.id{where}",
        values,
    ).fetchone()[0]
    pages = max(1, math.ceil(total / filters["per_page"]))
    page = min(filters["page"], pages)
    sort_column = {
        "created_at": "t.created_at",
        "type": "t.type",
        "amount_currency": "t.amount_currency",
        "amount": "CAST(t.amount AS REAL)",
        "native_amount": "CAST(t.native_amount AS REAL)",
        "status": "t.status",
        "category": category_sql,
    }[filters["sort"]]
    rows = connection.execute(
        f"SELECT t.*, {category_sql} AS display_category "
        f"FROM transactions t LEFT JOIN categories c ON c.transaction_id = t.id{where} "
        f"ORDER BY {sort_column} {filters['direction'].upper()}, t.id DESC "
        "LIMIT ? OFFSET ?",
        [*values, filters["per_page"], (page - 1) * filters["per_page"]],
    ).fetchall()
    return rows, total, pages, page


def transaction_navigation(filters, selected_date, page, pages):
    base = {
        **{key: value for key, value in filters.items() if key != "page"},
        "date": selected_date,
    }
    sort_urls = {}
    for column in ("created_at", "type", "amount_currency", "amount", "status", "category"):
        params = dict(base)
        params["sort"] = column
        params["direction"] = (
            "asc"
            if filters["sort"] == column and filters["direction"] == "desc"
            else "desc"
        )
        params["page"] = 1
        sort_urls[column] = url_for("dashboard", **params) + "#transactions"
    previous_params = dict(base, page=max(1, page - 1))
    next_params = dict(base, page=min(pages, page + 1))
    return {
        "sort_urls": sort_urls,
        "previous_url": url_for("dashboard", **previous_params) + "#transactions",
        "next_url": url_for("dashboard", **next_params) + "#transactions",
        "clear_filters_url": url_for("dashboard", date=selected_date) + "#transactions",
    }


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("APP_SECRET_KEY"),
        APP_PASSWORD=os.getenv("APP_PASSWORD"),
        DATABASE_PATH=os.getenv(
            "DATABASE_PATH", str(Path(app.instance_path) / "crypto-dashboard.sqlite3")
        ),
        COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("TESTING") and (
        not app.config.get("SECRET_KEY") or not app.config.get("APP_PASSWORD")
    ):
        raise RuntimeError("Set APP_SECRET_KEY and APP_PASSWORD before starting the app.")

    register_template_filters(app)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    initialize_database(app.config["DATABASE_PATH"])

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def require_login():
        if request.endpoint in {"login", "healthz", "static"}:
            return None
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentification requise."}), 401
            return redirect(url_for("login", next=request.path))
        return None

    def require_csrf():
        supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(supplied or "", expected):
            return jsonify({"error": "Jeton de sécurité invalide. Rechargez la page."}), 400
        return None

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if session.get("authenticated"):
            return redirect(url_for("dashboard"))
        session.setdefault("csrf_token", secrets.token_urlsafe(32))
        error = None
        if request.method == "POST":
            if not hmac.compare_digest(
                request.form.get("csrf_token", ""), session.get("csrf_token", "")
            ):
                return "Jeton de sécurité invalide. Rechargez la page.", 400
            supplied = request.form.get("password", "")
            if hmac.compare_digest(
                supplied.encode("utf-8"), app.config["APP_PASSWORD"].encode("utf-8")
            ):
                session.clear()
                session["authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(32)
                session.permanent = True
                next_path = request.args.get("next", "")
                if next_path.startswith("/") and not next_path.startswith("//"):
                    return redirect(next_path)
                return redirect(url_for("dashboard"))
            error = "Mot de passe incorrect."
        return render_template("login.html", error=error, csrf_token=session["csrf_token"])

    @app.post("/logout")
    def logout():
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def dashboard():
        csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
        filters = parse_transaction_filters(request.args)
        try:
            selected_date = datetime.strptime(
                request.args.get("date", datetime.now().date().isoformat()), "%Y-%m-%d"
            ).date().isoformat()
        except ValueError:
            abort(400, "Format de date invalide.")
        if selected_date > datetime.now().date().isoformat():
            abort(400, "La date ne peut pas être dans le futur.")
        connection = get_connection(app.config["DATABASE_PATH"])
        try:
            migrate_market_price_cache(connection)
            rows, transaction_count, transaction_pages, current_page = query_transactions(
                connection, filters
            )
            history = connection.execute(
                "SELECT MIN(substr(created_at, 1, 10)) AS first_date, "
                "MAX(substr(created_at, 1, 10)) AS last_date FROM transactions"
            ).fetchone()
            navigation = transaction_navigation(
                filters, selected_date, current_page, transaction_pages
            )
            last_sync = read_setting(connection, "last_sync")
            sync_error = read_setting(connection, "sync_error")
            complete = read_setting(connection, "portfolio_complete") == "true"
            if not os.getenv("COINBASE_API_KEY_NAME") or not os.getenv(
                "COINBASE_API_PRIVATE_KEY"
            ):
                connection.close()
                return render_template(
                    "dashboard.html",
                    dashboard_data=None,
                    transactions=[],
                    last_sync=last_sync,
                    sync_error=sync_error,
                    connected=False,
                    csrf_token=csrf_token,
                    complete=complete,
                    categories=CATEGORIES,
                    category_overrides={},
                    current_date=datetime.now().date().isoformat(),
                    filters=filters,
                    transaction_count=transaction_count,
                    transaction_pages=transaction_pages,
                    current_page=current_page,
                    filter_params={
                        **{key: value for key, value in filters.items() if key != "page"},
                        "date": selected_date,
                    },
                    transaction_types=[],
                    history_start=history["first_date"],
                    history_end=history["last_date"],
                    **navigation,
                )
            all_rows = connection.execute(
                "SELECT * FROM transactions ORDER BY created_at ASC"
            ).fetchall()
            overrides = {
                row["transaction_id"]: row["category"]
                for row in connection.execute("SELECT transaction_id, category FROM categories")
            }
            account_balances, account_balances_at = get_account_balances(connection)
            if selected_date != datetime.now().date().isoformat():
                account_balances = None
                account_balances_at = None
            required_quotes = defaultdict(set)
            for row in all_rows:
                tx_date = row["created_at"][:10]
                if tx_date > selected_date:
                    continue
                if row["native_currency"] in FIAT_CURRENCIES - {"EUR"}:
                    required_quotes[row["native_currency"]].add(tx_date)
                if row["amount_currency"] not in FIAT_CURRENCIES:
                    valuation_asset = canonical_asset(row["amount_currency"])
                    required_quotes[valuation_asset].add(selected_date)
                    if row["type"] in {
                        "earn_payout", "staking_reward", "interest",
                        "incentives_rewards_payout", "subscription_rebate",
                    } and decimal_value(row["native_amount"]) in (None, Decimal("0")):
                        required_quotes[valuation_asset].add(tx_date)
                    if (
                        row["type"] == "receive"
                        or (
                            row["type"] == "send"
                            and decimal_value(row["amount"]) is not None
                            and decimal_value(row["amount"]) > Decimal("0")
                        )
                    ):
                        required_quotes[valuation_asset].add(tx_date)
            market = FallbackMarketData(
                FallbackMarketData(
                    CoinbaseMarketData(),
                    BinanceMarketData(),
                ),
                CoinGeckoMarketData(os.getenv("COINGECKO_API_KEY")),
            )
            price_cache = {}
            chart_price_cache = {}
            price_errors = {}

            def price_for(asset, day):
                key = (asset, day)
                if key not in price_cache:
                    found, cached, cached_at = get_cached_price(connection, asset, day)
                    if day == datetime.now().date().isoformat() and cached_at:
                        found = (
                            cached_price_is_fresh(cached_at, 300)
                            and cached is not None
                        )
                    elif found and cached is None:
                        found = cached_price_is_fresh(cached_at, 300)
                    if found:
                        price_cache[key] = decimal_value(cached)
                    else:
                        try:
                            price_cache[key] = market.price_eur(
                                asset, datetime.strptime(day, "%Y-%m-%d").date()
                            )
                            cache_price(connection, asset, day, price_cache[key])
                        except MarketDataError as error:
                            price_cache[key] = None
                            price_errors[key] = str(error)
                return price_cache[key]

            def prefetch_prices(requirements):
                for asset, days in requirements.items():
                    missing_days = []
                    for day in days:
                        if day == datetime.now().date().isoformat():
                            continue
                        found, cached, cached_at = get_cached_price(connection, asset, day)
                        if found and cached is None:
                            found = cached_price_is_fresh(cached_at, 300)
                        if not found:
                            missing_days.append(day)
                        else:
                            chart_price_cache[(asset, day)] = decimal_value(cached)
                    if not missing_days:
                        continue
                    try:
                        prices = market.daily_closes(asset, missing_days)
                        chart_price_cache.update(
                            {
                                (asset, day): prices.get(day)
                                for day in missing_days
                            }
                        )
                        cache_prices(
                            connection,
                            asset,
                            {day: prices.get(day) for day in missing_days},
                        )
                    except MarketDataError as error:
                        chart_price_cache.update(
                            {(asset, day): None for day in missing_days}
                        )
                        price_errors.update(
                            {
                                (asset, day): str(error)
                                for day in missing_days
                            }
                        )

            price_for.prefetch = prefetch_prices
            price_for.chart_price = lambda asset, day: (
                price_cache[(asset, day)]
                if (asset, day) in price_cache
                else chart_price_cache.get((asset, day))
            )
            prefetch_prices(required_quotes)
            data = build_dashboard(
                all_rows,
                overrides,
                as_of=selected_date,
                complete=complete,
                price_for=price_for,
                account_balances=account_balances,
                account_balances_at=account_balances_at,
            )
            market_issues = build_market_quality_issues(
                price_errors,
                {**chart_price_cache, **price_cache},
                all_rows,
                selected_date,
            )
            unavailable_assets_today = {
                issue["asset"]
                for issue in market_issues
                if selected_date in issue.get("dates", [])
            }
            if unavailable_assets_today:
                data["quality_issues"] = [
                    issue for issue in data["quality_issues"]
                    if not (
                        issue["kind"] == "Cours EUR actuel manquant"
                        and issue["asset"] in unavailable_assets_today
                    )
                ]
            data["quality_issues"].extend(market_issues)
            data["quality_report"] = summarize_quality_issues(data["quality_issues"])
            if market_issues:
                data["warnings"].append(
                    "Cours EUR indisponibles pour "
                    + ", ".join(sorted({issue["asset"] for issue in market_issues}))
                    + " : consultez la section Qualité des données pour les détails."
                )
            transaction_rows = []
            for row in rows:
                transaction = dict(row)
                normalized = normalized_transaction(transaction, overrides)
                transaction["value_eur"] = _eur_value(normalized, price_for)
                transaction_rows.append(transaction)
            return render_template(
                "dashboard.html",
                dashboard_data=data,
                transactions=transaction_rows,
                last_sync=last_sync,
                sync_error=sync_error,
                connected=True,
                csrf_token=csrf_token,
                complete=complete,
                categories=CATEGORIES,
                category_overrides=overrides,
                current_date=datetime.now().date().isoformat(),
                filters=filters,
                transaction_count=transaction_count,
                transaction_pages=transaction_pages,
                current_page=current_page,
                filter_params={
                    **{key: value for key, value in filters.items() if key != "page"},
                    "date": selected_date,
                },
                transaction_types=[
                    row["type"]
                    for row in connection.execute(
                        "SELECT DISTINCT type FROM transactions ORDER BY type"
                    )
                ],
                history_start=history["first_date"],
                history_end=history["last_date"],
                **navigation,
            )
        finally:
            connection.close()

    @app.post("/api/sync")
    def sync():
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        try:
            client = CoinbaseClient.from_environment()
            accounts = client.fetch_accounts()
            transactions = client.fetch_all_transactions(accounts)
            connection = get_connection(app.config["DATABASE_PATH"])
            try:
                store_transactions(connection, transactions)
                store_account_balances(connection, accounts)
                set_setting(connection, "last_sync", datetime.now().astimezone().isoformat())
                set_setting(connection, "sync_error", "")
                connection.commit()
            finally:
                connection.close()
            dates = [transaction.get("created_at", "")[:10] for transaction in transactions]
            return jsonify({
                "count": len(transactions),
                "first_date": min(dates) if dates else None,
                "last_date": max(dates) if dates else None,
                "message": "Synchronisation terminée.",
            })
        except CoinbaseError as error:
            connection = get_connection(app.config["DATABASE_PATH"])
            try:
                set_setting(connection, "sync_error", str(error))
                connection.commit()
            finally:
                connection.close()
            return jsonify({"error": str(error)}), 502

    @app.post("/api/transactions/<transaction_id>/category")
    def update_category(transaction_id):
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        payload = request.get_json(silent=True)
        category = payload.get("category") if isinstance(payload, dict) else None
        if category not in CATEGORIES:
            return jsonify({"error": "Catégorie invalide."}), 400
        connection = get_connection(app.config["DATABASE_PATH"])
        try:
            exists = connection.execute(
                "SELECT 1 FROM transactions WHERE id = ?", (transaction_id,)
            ).fetchone()
            if not exists:
                return jsonify({"error": "Transaction introuvable."}), 404
            connection.execute(
                "INSERT INTO categories(transaction_id, category) VALUES (?, ?) "
                "ON CONFLICT(transaction_id) DO UPDATE SET category = excluded.category",
                (transaction_id, category),
            )
            connection.commit()
        finally:
            connection.close()
        return jsonify({"category": category})

    @app.post("/api/portfolio-complete")
    def set_portfolio_complete():
        csrf_error = require_csrf()
        if csrf_error:
            return csrf_error
        payload = request.get_json(silent=True)
        value = payload.get("complete") if isinstance(payload, dict) else None
        if not isinstance(value, bool):
            return jsonify({"error": "Valeur invalide."}), 400
        connection = get_connection(app.config["DATABASE_PATH"])
        try:
            set_setting(connection, "portfolio_complete", "true" if value else "false")
            connection.commit()
        finally:
            connection.close()
        return jsonify({"complete": value})

    return app
