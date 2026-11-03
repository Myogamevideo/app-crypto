const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content ?? "";
const toast = document.getElementById("toast");
let toastTimer;

function showToast(message, isError = false) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.toggle("toast-error", isError);
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 3500);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
    body: JSON.stringify(body),
    credentials: "same-origin",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Une erreur est survenue.");
  return data;
}

document.getElementById("sync-button")?.addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.classList.add("is-loading");
  button.querySelector(".sync-icon").textContent = "…";
  try {
    const response = await fetch("/api/sync", {
      method: "POST",
      headers: { "X-CSRF-Token": csrfToken },
      credentials: "same-origin",
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "La synchronisation a échoué.");
    const coverage = data.first_date && data.last_date
      ? ` (${data.first_date} – ${data.last_date})`
      : "";
    showToast(`${data.count} transactions synchronisées${coverage}.`);
    window.setTimeout(() => window.location.reload(), 700);
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
    button.classList.remove("is-loading");
    button.querySelector(".sync-icon").textContent = "↻";
  }
});

document.querySelectorAll(".category-select").forEach((select) => {
  select.addEventListener("change", async () => {
    try {
      await postJson(`/api/transactions/${encodeURIComponent(select.dataset.transactionId)}/category`, {
        category: select.value,
      });
      showToast("Catégorie mise à jour.");
      window.setTimeout(() => window.location.reload(), 500);
    } catch (error) {
      showToast(error.message, true);
      window.location.reload();
    }
  });
});

document.getElementById("portfolio-complete")?.addEventListener("change", async (event) => {
  try {
    await postJson("/api/portfolio-complete", { complete: event.currentTarget.checked });
    showToast("Paramètre fiscal mis à jour.");
    window.setTimeout(() => window.location.reload(), 500);
  } catch (error) {
    showToast(error.message, true);
    event.currentTarget.checked = !event.currentTarget.checked;
  }
});

document.getElementById("as-of-date")?.addEventListener("change", (event) => {
  const selected = event.currentTarget.value;
  if (selected) {
    const url = new URL(window.location.href);
    url.searchParams.set("date", selected);
    url.hash = "overview";
    window.location.assign(url);
  }
});
