from calendar import monthrange
from datetime import date
from decimal import Decimal


ZERO = Decimal("0")
CHART_WIDTH = 720
CHART_HEIGHT = 240
CHART_MARGIN = {"top": 18, "right": 14, "bottom": 35, "left": 58}


def _short_eur(value):
    amount = Decimal(str(value))
    if abs(amount) >= Decimal("1000"):
        return f"{amount / Decimal('1000'):.0f} k€"
    return f"{amount:.0f} €"


def _chart_axes(points, keys):
    width = CHART_WIDTH - CHART_MARGIN["left"] - CHART_MARGIN["right"]
    height = CHART_HEIGHT - CHART_MARGIN["top"] - CHART_MARGIN["bottom"]
    values = [
        Decimal(str(point[key]))
        for point in points
        for key in keys
        if point.get(key) is not None
    ]
    if not values:
        return None

    low, high = min(ZERO, min(values)), max(ZERO, max(values))
    if low == high:
        high = low + Decimal("1")
    span = high - low

    def x_position(index):
        if len(points) < 2:
            return CHART_MARGIN["left"] + width / 2
        return CHART_MARGIN["left"] + width * index / (len(points) - 1)

    def y_position(value):
        return CHART_MARGIN["top"] + height * float((high - value) / span)

    y_ticks = []
    for index in range(5):
        value = high - span * Decimal(index) / Decimal("4")
        y_ticks.append({"y": y_position(value), "label": _short_eur(value)})

    tick_count = min(5, len(points))
    x_ticks = []
    for index in range(tick_count):
        point_index = round(index * (len(points) - 1) / max(tick_count - 1, 1))
        x_ticks.append({
            "x": x_position(point_index),
            "label": points[point_index]["date"][:7],
        })
    return {
        "width": CHART_WIDTH,
        "height": CHART_HEIGHT,
        "left": CHART_MARGIN["left"],
        "right": CHART_WIDTH - CHART_MARGIN["right"],
        "top": CHART_MARGIN["top"],
        "bottom": CHART_HEIGHT - CHART_MARGIN["bottom"],
        "y_ticks": y_ticks,
        "x_ticks": x_ticks,
        "x_position": x_position,
        "y_position": y_position,
    }


def _line_chart(points, series):
    axes = _chart_axes(points, [item["key"] for item in series])
    if axes is None:
        return None
    paths = []
    for item in series:
        segments = []
        current_segment = []
        for index, point in enumerate(points):
            value = point.get(item["key"])
            if value is None:
                if current_segment:
                    segments.append(current_segment)
                    current_segment = []
                continue
            current_segment.append(
                f'{axes["x_position"](index):.2f},{axes["y_position"](Decimal(str(value))):.2f}'
            )
        if current_segment:
            segments.append(current_segment)
        path_data = " ".join(
            "M " + " L ".join(segment)
            for segment in segments
        )
        paths.append({**item, "path": path_data})
    return {**axes, "paths": paths}


def _monthly_flow_chart(transactions, value_for):
    months = {}
    valid_transactions = [
        tx for tx in transactions
        if len(tx.get("date", "")) >= 7 and tx.get("category") in {"BUY", "SELL"}
    ]
    if not valid_transactions:
        return None
    first_month = min(tx["date"][:7] for tx in valid_transactions)
    last_month = max(tx["date"][:7] for tx in valid_transactions)
    first_date = date.fromisoformat(f"{first_month}-01")
    last_date = date.fromisoformat(f"{last_month}-01")
    current = first_date
    while current <= last_date:
        key = current.strftime("%Y-%m")
        months[key] = {"date": key, "buy": ZERO, "sell": ZERO, "complete": True}
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )

    for tx in valid_transactions:
        month = months[tx["date"][:7]]
        value = value_for(tx)
        if value is None:
            month["complete"] = False
        elif tx["category"] == "BUY":
            month["buy"] += value
        else:
            month["sell"] -= value
    return [months[key] for key in sorted(months)]


def _bar_chart(points):
    axes = _chart_axes(points, ["buy", "sell"])
    if axes is None:
        return None
    baseline = axes["y_position"](ZERO)
    plot_width = axes["right"] - axes["left"]
    group_width = plot_width / max(len(points), 1)
    bar_width = min(11, group_width * 0.34)
    bars = []
    for index, point in enumerate(points):
        center = axes["x_position"](index)
        for key, offset, color in (
            ("buy", -bar_width / 2, "#56a477"),
            ("sell", bar_width / 2, "#cb7770"),
        ):
            value = point.get(key)
            if value is None or Decimal(str(value)) == ZERO:
                continue
            y_value = axes["y_position"](Decimal(str(value)))
            bars.append({
                "x": center + offset,
                "y": min(baseline, y_value),
                "width": bar_width,
                "height": max(1, abs(baseline - y_value)),
                "color": color,
            })
    return {**axes, "baseline": baseline, "bars": bars}


def _month_end(year, month):
    return date(year, month, monthrange(year, month)[1])


def history_dates(transactions, cutoff):
    if not transactions:
        return []
    last = date.fromisoformat(cutoff)
    first = max(
        min(date.fromisoformat(tx["date"][:10]) for tx in transactions),
        _subtract_months(last, 12),
    )
    if first > last:
        return []
    dates = set()
    current = date(first.year, first.month, 1)
    while current <= last:
        month_end = _month_end(current.year, current.month)
        dates.add(min(month_end, last).isoformat())
        current = (
            date(current.year + 1, 1, 1)
            if current.month == 12
            else date(current.year, current.month + 1, 1)
        )
    dates.add(last.isoformat())
    for months in (1, 3, 6, 12):
        index = last.year * 12 + last.month - 1 - months
        year, month = divmod(index, 12)
        anchor = date(year, month + 1, min(last.day, monthrange(year, month + 1)[1]))
        if anchor >= first:
            dates.add(anchor.isoformat())
    return sorted(day for day in dates if day <= cutoff)


def _subtract_months(day, months):
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def build_dashboard_charts(history, assets, transactions, value_for, as_of):
    value_chart = _line_chart(history, [{"key": "value", "label": "Valeur du portefeuille", "color": "#2e805d"}])
    gains_chart = _line_chart(
        history,
        [
            {"key": "realized", "label": "Gains réalisés", "color": "#438b62"},
            {"key": "unrealized", "label": "Gains latents", "color": "#6d91c8"},
        ],
    )
    monthly_flows = _monthly_flow_chart(transactions, value_for)
    flow_chart = _bar_chart(monthly_flows) if monthly_flows else None

    priced_assets = [asset for asset in assets if asset["value_eur"] is not None]
    all_prices_available = len(priced_assets) == len(assets)
    known_basis_value = sum(
        (asset["value_eur"] for asset in priced_assets if asset["basis_known"]),
        ZERO,
    )
    unknown_basis_value = sum(
        (asset["value_eur"] for asset in priced_assets if not asset["basis_known"]),
        ZERO,
    )
    total_value = sum((asset["value_eur"] for asset in priced_assets), ZERO)
    basis_coverage = (
        known_basis_value / total_value * 100
        if all_prices_available and total_value > ZERO
        else None
    )
    allocation = []
    largest_value = max(
        (asset["value_eur"] for asset in priced_assets),
        default=ZERO,
    )
    for asset in priced_assets:
        allocation.append({
            "asset": asset["asset"],
            "value_eur": asset["value_eur"],
            "share_percent": (
                asset["value_eur"] / total_value * 100
                if all_prices_available and total_value > ZERO
                else None
            ),
            "bar_percent": (
                asset["value_eur"] / largest_value * 100
                if largest_value > ZERO
                else ZERO
            ),
        })

    last_day = date.fromisoformat(as_of)
    end_point = next(
        (point for point in reversed(history) if point["date"] == as_of),
        None,
    )
    performance_periods = []
    if end_point:
        end_performance = (
            end_point["realized"] + end_point["unrealized"]
            if end_point["realized"] is not None and end_point["unrealized"] is not None
            else None
        )
        for months, label in ((1, "1 mois"), (3, "3 mois"), (6, "6 mois"), (12, "1 an")):
            start_day = _subtract_months(last_day, months).isoformat()
            start_point = next(
                (point for point in history if point["date"] == start_day),
                None,
            )
            start_performance = (
                start_point["realized"] + start_point["unrealized"]
                if start_point
                and start_point["realized"] is not None
                and start_point["unrealized"] is not None
                else None
            )
            performance_periods.append({
                "label": label,
                "value_eur": (
                    end_performance - start_performance
                    if end_performance is not None and start_performance is not None
                    else None
                ),
            })
        performance_periods.append({
            "label": "Depuis le début",
            "value_eur": end_performance,
        })

    return {
        "value_chart": value_chart,
        "gains_chart": gains_chart,
        "flow_chart": flow_chart,
        "allocation": allocation,
        "allocation_complete": all_prices_available,
        "basis_coverage_percent": basis_coverage,
        "unknown_basis_value_eur": unknown_basis_value if all_prices_available else None,
        "performance_periods": performance_periods,
        "history_price_incomplete": any(
            point["value"] is None for point in history
        ),
        "history_months": len(history),
        "flow_incomplete": any(
            not point["complete"] for point in (monthly_flows or [])
        ),
    }
