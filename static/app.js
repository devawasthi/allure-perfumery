const CART_KEY = "the-scentist-cart-v1";
const WISHLIST_KEY = "the-scentist-wishlist-v1";
const THEME_KEY = "the-scentist-theme";
const SHIPPING_FEE = 350;
const FREE_SHIPPING_THRESHOLD = 12500;

const money = (value) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value || 0);

const INDIA_CITY_OPTIONS = {
  "Andaman and Nicobar Islands": ["Port Blair"],
  "Andhra Pradesh": ["Amaravati", "Guntur", "Kakinada", "Nellore", "Tirupati", "Vijayawada", "Visakhapatnam"],
  "Arunachal Pradesh": ["Itanagar", "Naharlagun", "Pasighat", "Tawang"],
  Assam: ["Dibrugarh", "Guwahati", "Jorhat", "Silchar", "Tezpur"],
  Bihar: ["Bhagalpur", "Gaya", "Muzaffarpur", "Patna"],
  Chandigarh: ["Chandigarh"],
  Chhattisgarh: ["Bhilai", "Bilaspur", "Korba", "Raipur"],
  "Dadra and Nagar Haveli and Daman and Diu": ["Daman", "Diu", "Silvassa"],
  Delhi: ["Delhi", "New Delhi"],
  Goa: ["Margao", "Panaji", "Vasco da Gama"],
  Gujarat: ["Ahmedabad", "Anand", "Gandhinagar", "Rajkot", "Surat", "Vadodara"],
  Haryana: ["Ambala", "Faridabad", "Gurugram", "Hisar", "Karnal", "Panipat", "Rohtak"],
  "Himachal Pradesh": ["Dharamshala", "Mandi", "Shimla", "Solan"],
  "Jammu and Kashmir": ["Jammu", "Srinagar"],
  Jharkhand: ["Bokaro", "Dhanbad", "Jamshedpur", "Ranchi"],
  Karnataka: ["Bengaluru", "Hubballi", "Mangaluru", "Mysuru", "Udupi"],
  Kerala: ["Kochi", "Kollam", "Kozhikode", "Thiruvananthapuram", "Thrissur"],
  Ladakh: ["Kargil", "Leh"],
  Lakshadweep: ["Kavaratti"],
  "Madhya Pradesh": ["Bhopal", "Gwalior", "Indore", "Jabalpur", "Ujjain"],
  Maharashtra: ["Mumbai", "Nagpur", "Nashik", "Pune", "Thane"],
  Manipur: ["Imphal"],
  Meghalaya: ["Shillong", "Tura"],
  Mizoram: ["Aizawl"],
  Nagaland: ["Dimapur", "Kohima"],
  Odisha: ["Bhubaneswar", "Cuttack", "Puri", "Rourkela"],
  Puducherry: ["Karaikal", "Puducherry"],
  Punjab: ["Amritsar", "Jalandhar", "Ludhiana", "Mohali", "Patiala"],
  Rajasthan: ["Ajmer", "Bikaner", "Jaipur", "Jodhpur", "Kota", "Udaipur"],
  Sikkim: ["Gangtok"],
  "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Salem", "Tiruchirappalli"],
  Telangana: ["Hyderabad", "Karimnagar", "Secunderabad", "Warangal"],
  Tripura: ["Agartala"],
  "Uttar Pradesh": ["Agra", "Ghaziabad", "Kanpur", "Lucknow", "Noida", "Prayagraj", "Varanasi"],
  Uttarakhand: ["Dehradun", "Haridwar", "Nainital", "Rishikesh"],
  "West Bengal": ["Asansol", "Durgapur", "Howrah", "Kolkata", "Siliguri"],
};

const toastStack = document.querySelector("[data-toast-stack]");

function showToast(message) {
  if (!toastStack) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  toastStack.appendChild(node);
  window.setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(12px)";
  }, 2600);
  window.setTimeout(() => node.remove(), 3200);
}

function preferredTheme() {
  const savedTheme = window.localStorage.getItem(THEME_KEY);
  if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
  const nextTheme = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = nextTheme;
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.setAttribute("aria-label", `Switch to ${nextTheme === "light" ? "dark" : "light"} mode`);
    button.setAttribute("aria-pressed", nextTheme === "light" ? "true" : "false");
  });
  document.querySelectorAll("[data-theme-icon]").forEach((node) => {
    node.textContent = nextTheme === "light" ? "☾" : "☼";
  });
}

function mountThemeToggle() {
  applyTheme(preferredTheme());
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const currentTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
      const nextTheme = currentTheme === "light" ? "dark" : "light";
      window.localStorage.setItem(THEME_KEY, nextTheme);
      applyTheme(nextTheme);
    });
  });
}

function readCart() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CART_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => ({
        variant_id: Number(item.variant_id),
        quantity: Number(item.quantity),
      }))
      .filter((item) => Number.isInteger(item.variant_id) && item.variant_id > 0 && item.quantity > 0);
  } catch {
    return [];
  }
}

function writeCart(items) {
  window.localStorage.setItem(CART_KEY, JSON.stringify(items));
  updateCartCount();
}

function updateCartCount() {
  const count = readCart().reduce((sum, item) => sum + item.quantity, 0);
  document.querySelectorAll("[data-cart-count]").forEach((node) => {
    node.textContent = String(count);
  });
}

function addToCart(variantId, label) {
  const cart = readCart();
  const current = cart.find((item) => item.variant_id === variantId);
  if (current) {
    current.quantity += 1;
  } else {
    cart.push({ variant_id: variantId, quantity: 1 });
  }
  writeCart(cart);
  showToast(`${label} added to cart.`);
}

function readWishlist() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(WISHLIST_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => String(item || "").trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

function writeWishlist(items) {
  const uniqueItems = [...new Set(items.filter(Boolean))];
  window.localStorage.setItem(WISHLIST_KEY, JSON.stringify(uniqueItems));
  updateWishlistButtons();
}

function updateWishlistButtons() {
  const wishlist = new Set(readWishlist());
  document.querySelectorAll("[data-wishlist-toggle]").forEach((button) => {
    const isActive = wishlist.has(button.dataset.wishlistId || "");
    button.classList.toggle("is-active", isActive);
    button.setAttribute("aria-pressed", isActive ? "true" : "false");
    const label = button.dataset.itemLabel || "This fragrance";
    button.setAttribute(
      "aria-label",
      `${isActive ? "Remove" : "Add"} ${label} ${isActive ? "from" : "to"} wishlist`
    );
  });
}

function toggleWishlist(itemId, label) {
  if (!itemId) return;
  const wishlist = readWishlist();
  const exists = wishlist.includes(itemId);
  const nextWishlist = exists
    ? wishlist.filter((item) => item !== itemId)
    : [...wishlist, itemId];
  writeWishlist(nextWishlist);
  showToast(`${label || "Fragrance"} ${exists ? "removed from" : "added to"} wishlist.`);
}

function updateQuantity(variantId, delta) {
  const cart = readCart()
    .map((item) =>
      item.variant_id === variantId
        ? { ...item, quantity: Math.max(0, item.quantity + delta) }
        : item
    )
    .filter((item) => item.quantity > 0);
  writeCart(cart);
}

function removeItem(variantId) {
  const cart = readCart().filter((item) => item.variant_id !== variantId);
  writeCart(cart);
}

function shippingFor(subtotal) {
  return subtotal >= FREE_SHIPPING_THRESHOLD ? 0 : SHIPPING_FEE;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Request failed.");
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function fetchCartItems(cart) {
  if (!cart.length) return [];
  const ids = cart.map((item) => item.variant_id).join(",");
  const payload = await fetchJson(`/api/cart-items?variant_ids=${ids}`);
  const itemMap = new Map((payload.items || []).map((item) => [item.variant_id, item]));
  return cart
    .map((item) => {
      const detail = itemMap.get(item.variant_id);
      if (!detail) return null;
      return { ...detail, quantity: item.quantity, line_total: item.quantity * detail.price_inr };
    })
    .filter(Boolean);
}

function renderCartSummary(items, subtotalNode, shippingNode, totalNode) {
  const subtotal = items.reduce((sum, item) => sum + item.line_total, 0);
  const shipping = shippingFor(subtotal);
  const total = subtotal + shipping;

  if (subtotalNode) subtotalNode.textContent = money(subtotal);
  if (shippingNode) shippingNode.textContent = shipping === 0 ? "Free" : money(shipping);
  if (totalNode) totalNode.textContent = money(total);

  return { subtotal, shipping, total };
}

function imageMarkup(item, className = "fragrance-thumb") {
  const imageUrl = item.photo_icon_url || item.image_url;
  return `<img class="${escapeHtml(className)}" src="${escapeHtml(imageUrl)}" alt="${escapeHtml(
    `${item.brand} ${item.name}`
  )}" loading="lazy" />`;
}

function checkoutIdempotencyKey() {
  const storageKey = "the-scentist-checkout-idempotency";
  let key = window.sessionStorage.getItem(storageKey);
  if (!key) {
    key = window.crypto?.randomUUID?.() || `checkout-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(storageKey, key);
  }
  return key;
}

function clearCheckoutIdempotencyKey() {
  window.sessionStorage.removeItem("the-scentist-checkout-idempotency");
}

function mountConcierge() {
  const root = document.querySelector("[data-concierge-root]");
  if (!root) return;

  const form = root.querySelector("[data-concierge-form]");
  const input = root.querySelector("[data-concierge-input]");
  const status = root.querySelector("[data-concierge-status]");
  const results = root.querySelector("[data-concierge-results]");
  const submitButton = form?.querySelector("button[type='submit']");

  function setConciergeStatus(message, isError = false) {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function renderConciergeResults(payload) {
    if (!results) return;
    const recommendations = payload.recommendations || [];
    if (!recommendations.length) {
      results.innerHTML = `
        <div class="empty-state empty-state--wide">
          <h2>No matching edit yet.</h2>
          <p>Try a broader budget, occasion, or note family.</p>
        </div>
      `;
      return;
    }

    results.innerHTML = `
      <div class="concierge-results__intro">
        <p class="section-kicker">${payload.mode === "ai" ? "AI Edit" : "Live Catalog Edit"}</p>
        <h3>${escapeHtml(payload.reply || "Here is the edit I would start with.")}</h3>
      </div>
      <div class="concierge-grid">
        ${recommendations
          .map(
            (item) => `
              <article class="concierge-card">
                <a class="concierge-card__image" href="${escapeHtml(item.product_path)}">
                  <img src="${escapeHtml(item.image_url)}" alt="${escapeHtml(`${item.brand} ${item.name}`)}" loading="lazy" />
                </a>
                <div class="concierge-card__body">
                  <p class="section-kicker">${escapeHtml(item.brand)}</p>
                  <h4><a href="${escapeHtml(item.product_path)}">${escapeHtml(item.name)}</a></h4>
                  <p>${escapeHtml(item.reason)}</p>
                  <div class="concierge-card__meta">
                    <span>${escapeHtml(item.variant_label)}</span>
                    <strong>${money(item.price_inr)}</strong>
                  </div>
                  <div class="concierge-card__actions">
                    <button
                      type="button"
                      class="button button--ghost"
                      data-add-to-cart
                      data-variant-id="${Number(item.variant_id)}"
                      data-item-label="${escapeHtml(`${item.brand} ${item.name} ${item.variant_label}`)}"
                    >
                      Add to cart
                    </button>
                    <a class="button" href="${escapeHtml(item.product_path)}">View</a>
                  </div>
                </div>
              </article>
            `
          )
          .join("")}
      </div>
    `;
  }

  root.querySelectorAll("[data-concierge-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!input) return;
      input.value = button.dataset.conciergePrompt || "";
      input.focus();
    });
  });

  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input?.value.trim() || "";
    if (message.length < 3) {
      setConciergeStatus("Tell us a little more.", true);
      return;
    }

    if (submitButton) submitButton.disabled = true;
    setConciergeStatus("Selecting from the live catalog...");
    try {
      const payload = await fetchJson("/api/concierge", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message }),
      });
      renderConciergeResults(payload);
      setConciergeStatus("");
    } catch (error) {
      setConciergeStatus(error.message, true);
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

async function mountCartPage() {
  const root = document.querySelector("[data-cart-root]");
  if (!root) return;

  const itemsContainer = root.querySelector("[data-cart-items]");
  const emptyNode = root.querySelector("[data-cart-empty]");
  const subtotalNode = root.querySelector("[data-cart-subtotal]");
  const shippingNode = root.querySelector("[data-cart-shipping]");
  const totalNode = root.querySelector("[data-cart-total]");
  const cart = readCart();

  if (!cart.length) {
    emptyNode?.classList.remove("is-hidden");
    if (itemsContainer) itemsContainer.innerHTML = "";
    renderCartSummary([], subtotalNode, shippingNode, totalNode);
    return;
  }

  const items = await fetchCartItems(cart);
  if (!items.length) {
    writeCart([]);
    mountCartPage();
    return;
  }

  emptyNode?.classList.add("is-hidden");
  if (itemsContainer) {
    itemsContainer.innerHTML = items
      .map(
        (item) => `
          <article class="cart-item">
            ${imageMarkup(item, "fragrance-thumb fragrance-thumb--cart")}
            <div class="cart-item__body">
              <strong>${escapeHtml(item.brand)} ${escapeHtml(item.name)}</strong>
              <p>${escapeHtml(item.size_label)} - ${escapeHtml(item.sale_type.replace(/_/g, " "))}</p>
              <div class="qty-control">
                <button type="button" data-cart-change="-1" data-variant-id="${item.variant_id}">-</button>
                <span>${item.quantity}</span>
                <button type="button" data-cart-change="1" data-variant-id="${item.variant_id}">+</button>
              </div>
            </div>
            <div class="cart-item__price">
              <strong>${money(item.line_total)}</strong>
              <div class="cart-item__unit">${item.quantity} x ${money(item.price_inr)}</div>
              <button type="button" data-cart-remove data-variant-id="${item.variant_id}">Remove</button>
            </div>
          </article>
        `
      )
      .join("");
  }

  renderCartSummary(items, subtotalNode, shippingNode, totalNode);
}

async function mountCheckoutPage() {
  const root = document.querySelector("[data-checkout-root]");
  if (!root) return;

  const form = root.querySelector("[data-checkout-form]");
  const status = root.querySelector("[data-checkout-status]");
  const lines = root.querySelector("[data-checkout-items]");
  const subtotalNode = root.querySelector("[data-checkout-subtotal]");
  const shippingNode = root.querySelector("[data-checkout-shipping]");
  const totalNode = root.querySelector("[data-checkout-total]");
  const submitButton = root.querySelector("[data-checkout-submit]");
  const methodSelect = form?.querySelector('select[name="payment_method"]');
  const razorpayEnabled = root.dataset.razorpayEnabled === "true";
  const onlinePaymentMethods = new Set(["UPI", "Netbanking", "Credit/Debit Card"]);
  const cart = readCart();

  if (!cart.length) {
    if (form) {
      form.innerHTML = `
        <div class="empty-state">
          <h2>Your cart is empty.</h2>
          <p>Add some decants or bottles before trying to place an order.</p>
          <a class="button" href="/catalog">Go to catalog</a>
        </div>
      `;
    }
    if (lines) lines.innerHTML = "<p class='checkout-form__status'>No items in cart.</p>";
    renderCartSummary([], subtotalNode, shippingNode, totalNode);
    return;
  }

  const items = await fetchCartItems(cart);
  const totals = renderCartSummary(items, subtotalNode, shippingNode, totalNode);

  if (lines) {
    lines.innerHTML = items
      .map(
        (item) => `
          <div class="checkout-line">
            ${imageMarkup(item, "fragrance-thumb fragrance-thumb--line")}
            <div class="checkout-line__content">
              <div class="checkout-line__top">
                <strong>${escapeHtml(item.brand)} ${escapeHtml(item.name)}</strong>
                <strong class="checkout-line__price">${money(item.line_total)}</strong>
              </div>
              <p>${escapeHtml(item.size_label)} - Qty ${Number(item.quantity)}</p>
            </div>
          </div>
        `
      )
      .join("");
  }

  if (!form || !submitButton || !methodSelect) return;

  const emailInput = form.querySelector('input[name="email"]');
  const phoneInput = form.querySelector('input[name="phone"]');
  const addressInput = form.querySelector('input[name="shipping_line1"]');
  const stateInput = form.querySelector('[name="state"]');
  const citySelect = form.querySelector("[data-city-select]");
  const cityInput = form.querySelector("[data-city-value]") || form.querySelector('[name="city"]');
  const cityOtherInput = form.querySelector("[data-city-other]");
  const postalInput = form.querySelector('input[name="postal_code"]');
  const countryInput = form.querySelector('[name="country"]');

  function appendOption(select, value, label = value) {
    if (!select) return;
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.appendChild(option);
  }

  function syncCityValue() {
    if (!citySelect || !cityInput) return;
    const isOtherCity = citySelect.value === "__other__";
    if (cityOtherInput) {
      cityOtherInput.hidden = !isOtherCity;
      cityOtherInput.required = isOtherCity;
      if (!isOtherCity) {
        cityOtherInput.value = "";
        cityOtherInput.setCustomValidity("");
      }
    }
    cityInput.value = isOtherCity ? cityOtherInput?.value.trim() || "" : citySelect.value;
  }

  function populateCitySelect() {
    if (!citySelect || !stateInput) return;
    const cities = INDIA_CITY_OPTIONS[stateInput.value] || [];
    citySelect.innerHTML = "";
    citySelect.disabled = !stateInput.value;
    appendOption(citySelect, "", stateInput.value ? "Select city" : "Select state first");
    cities.forEach((city) => appendOption(citySelect, city));
    if (stateInput.value) appendOption(citySelect, "__other__", "Other city / not listed");
    citySelect.dispatchEvent(new Event("custom-select:refresh"));
    syncCityValue();
  }

  populateCitySelect();
  stateInput?.addEventListener("change", () => {
    populateCitySelect();
    validateRequiredText(stateInput, "State");
    validateCity();
  });
  citySelect?.addEventListener("change", () => {
    syncCityValue();
    validateCity();
  });
  cityOtherInput?.addEventListener("input", () => {
    syncCityValue();
    validateCity();
  });

  function validateRequiredText(input, label, minLength = 2) {
    if (!input) return true;
    const value = input.value.trim();
    if (!value) {
      input.setCustomValidity(`${label} is required.`);
      return false;
    }
    if (value.length < minLength) {
      input.setCustomValidity(`${label} should be at least ${minLength} characters.`);
      return false;
    }
    input.setCustomValidity("");
    return true;
  }

  function validateEmail() {
    if (!emailInput) return true;
    const value = emailInput.value.trim();
    if (!value) {
      emailInput.setCustomValidity("Email is required.");
      return false;
    }
    const isValid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
    emailInput.setCustomValidity(isValid ? "" : "Please enter a valid email address.");
    return isValid;
  }

  function validatePhone() {
    if (!phoneInput) return true;
    const digits = phoneInput.value.replace(/\D+/g, "");
    if (!digits) {
      phoneInput.setCustomValidity("Phone number is required.");
      return false;
    }
    const isValid = digits.length >= 10 && digits.length <= 15;
    phoneInput.setCustomValidity(isValid ? "" : "Please enter a valid phone number.");
    return isValid;
  }

  function validatePostalCode() {
    if (!postalInput) return true;
    const value = postalInput.value.trim();
    if (!value) {
      postalInput.setCustomValidity("Postal code is required.");
      return false;
    }
    const isValid = /^[A-Za-z0-9 -]{5,12}$/.test(value);
    postalInput.setCustomValidity(isValid ? "" : "Please enter a valid postal code.");
    return isValid;
  }

  function validateCity() {
    syncCityValue();
    if (citySelect && !citySelect.value) {
      citySelect.setCustomValidity("City is required.");
      return false;
    }
    citySelect?.setCustomValidity("");
    if (citySelect?.value === "__other__") {
      return validateRequiredText(cityOtherInput, "City");
    }
    return validateRequiredText(cityInput, "City");
  }

  function runCheckoutValidation() {
    const checks = [
      validateRequiredText(addressInput, "Address", 8),
      validateRequiredText(stateInput, "State"),
      validateCity(),
      validateRequiredText(countryInput, "Country"),
      validateEmail(),
      validatePhone(),
      validatePostalCode(),
    ];
    return checks.every(Boolean);
  }

  [
    [emailInput, validateEmail],
    [phoneInput, validatePhone],
    [addressInput, () => validateRequiredText(addressInput, "Address", 8)],
    [stateInput, () => validateRequiredText(stateInput, "State")],
    [postalInput, validatePostalCode],
    [countryInput, () => validateRequiredText(countryInput, "Country")],
  ].forEach(([input, validator]) => {
    if (!input) return;
    input.addEventListener("input", validator);
    input.addEventListener("blur", validator);
  });

  function setStatus(message, isError = false) {
    if (!status) return;
    status.textContent = message;
    status.classList.toggle("is-error", Boolean(isError));
  }

  function refreshMethodCopy() {
    const method = methodSelect.value;
    if (onlinePaymentMethods.has(method)) {
      submitButton.textContent = "Continue to payment";
      return;
    }

    submitButton.textContent = "Place order";
  }

  refreshMethodCopy();
  methodSelect.addEventListener("change", refreshMethodCopy);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    if (!runCheckoutValidation()) {
      form.reportValidity();
      setStatus("Please fix the required checkout fields before continuing.", true);
      return;
    }

    const customer = Object.fromEntries(new FormData(form).entries());
    const payload = {
      customer,
      items: readCart(),
      totals,
    };
    const idempotencyKey = checkoutIdempotencyKey();

    submitButton.disabled = true;
    const isOnlinePayment = onlinePaymentMethods.has(customer.payment_method);
    setStatus(isOnlinePayment ? "Preparing payment..." : "Placing order...");

    try {
      if (isOnlinePayment) {
        if (!razorpayEnabled || typeof window.Razorpay !== "function") {
          throw new Error("Online payment is not available on this deployment.");
        }

        const checkoutPayload = await fetchJson("/api/checkout/razorpay-order", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify(payload),
        });

        const checkout = checkoutPayload.checkout;
        const razorpayInstance = new window.Razorpay({
          key: checkout.razorpay_key_id,
          amount: checkout.amount_subunits,
          currency: checkout.currency,
          name: root.dataset.storeName || "The Scentist",
          description: "Luxury fragrance order",
          image: root.dataset.storeLogo || "",
          order_id: checkout.gateway_order_id,
          prefill: {
            name: checkout.customer_name,
            email: checkout.email,
            contact: checkout.phone,
          },
          notes: {
            local_order_number: checkout.local_order_number,
          },
          theme: {
            color: "#b47a4d",
          },
          modal: {
            ondismiss: () => {
              setStatus(
                `Payment window closed. Pending order ${checkout.local_order_number} is still saved if you want to retry.`
              );
              submitButton.disabled = false;
            },
          },
          handler: async (response) => {
            try {
              setStatus("Verifying payment...");
              const verification = await fetchJson("/api/payments/razorpay/verify", {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({
                  local_order_number: checkout.local_order_number,
                  razorpay_order_id: response.razorpay_order_id,
                  razorpay_payment_id: response.razorpay_payment_id,
                  razorpay_signature: response.razorpay_signature,
                }),
              });
              writeCart([]);
              clearCheckoutIdempotencyKey();
              window.location.assign(verification.order.public_path || `/order/${verification.order.order_number}`);
            } catch (error) {
              setStatus(error.message, true);
              submitButton.disabled = false;
            }
          },
        });

        razorpayInstance.on("payment.failed", async (failure) => {
          const description =
            failure?.error?.description || "Payment failed before verification could complete.";
          try {
            await fetchJson("/api/payments/razorpay/failure", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                local_order_number: checkout.local_order_number,
                razorpay_order_id: checkout.gateway_order_id,
                reason: description,
              }),
            });
          } catch {
            // The admin order list will still show the pending order if this update fails.
          }
          setStatus(`${description} The reserved stock has been released; please try checkout again.`, true);
          clearCheckoutIdempotencyKey();
          submitButton.disabled = false;
        });

        razorpayInstance.open();
        return;
      }

      const result = await fetchJson("/api/orders", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(payload),
      });

      writeCart([]);
      clearCheckoutIdempotencyKey();
      window.location.assign(result.order.public_path || `/order/${result.order.order_number}`);
    } catch (error) {
      setStatus(error.message, true);
      submitButton.disabled = false;
    }
  });
}

function mountReveals() {
  const nodes = document.querySelectorAll("[data-reveal]");
  if (!nodes.length) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.15 }
  );

  nodes.forEach((node) => observer.observe(node));
}

function mountGatewayTilt() {
  document.querySelectorAll(".gateway-card").forEach((card) => {
    card.addEventListener("pointermove", (event) => {
      const rect = card.getBoundingClientRect();
      const x = ((event.clientX - rect.left) / rect.width - 0.5) * 8;
      const y = ((event.clientY - rect.top) / rect.height - 0.5) * -8;
      card.style.setProperty("--tilt-x", `${x.toFixed(2)}deg`);
      card.style.setProperty("--tilt-y", `${y.toFixed(2)}deg`);
    });

    card.addEventListener("pointerleave", () => {
      card.style.setProperty("--tilt-x", "0deg");
      card.style.setProperty("--tilt-y", "0deg");
    });
  });
}

function mountCustomSelects() {
  if (window.matchMedia("(max-width: 760px), (pointer: coarse)").matches) return;
  document.querySelectorAll("select[data-custom-select]").forEach((select, index) => {
    if (select.dataset.customSelectMounted === "true") return;
    const shell = select.closest(".select-shell");
    if (!shell) return;

    select.dataset.customSelectMounted = "true";
    shell.classList.add("is-enhanced");

    const custom = document.createElement("div");
    const button = document.createElement("button");
    const list = document.createElement("div");
    const value = document.createElement("span");
    const listId = `custom-select-list-${index}-${select.name || "filter"}`;

    custom.className = "custom-select";
    if (select.dataset.filterList === "true") {
      custom.classList.add("custom-select--inline-list");
    }
    button.type = "button";
    button.className = "custom-select__button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", listId);
    list.id = listId;
    list.className = "custom-select__list";
    list.setAttribute("role", "listbox");

    button.appendChild(value);
    custom.append(button, list);
    shell.appendChild(custom);

    function selectedOption() {
      return select.options[select.selectedIndex] || select.options[0];
    }

    function close() {
      custom.classList.remove("is-open");
      button.setAttribute("aria-expanded", "false");
    }

    function syncLabel() {
      const option = selectedOption();
      value.textContent = option ? option.textContent.trim() : "";
      button.disabled = select.disabled;
      list.querySelectorAll(".custom-select__option").forEach((optionButton) => {
        const isSelected = optionButton.dataset.value === select.value;
        optionButton.classList.toggle("is-selected", isSelected);
        optionButton.setAttribute("aria-selected", isSelected ? "true" : "false");
      });
    }

    function rebuildOptions() {
      list.innerHTML = "";
      Array.from(select.options).forEach((option) => {
        const optionButton = document.createElement("button");
        optionButton.type = "button";
        optionButton.className = "custom-select__option";
        optionButton.dataset.value = option.value;
        optionButton.disabled = option.disabled;
        optionButton.textContent = option.textContent.trim();
        optionButton.setAttribute("role", "option");
        optionButton.addEventListener("click", () => {
          if (option.disabled) return;
          select.value = option.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          syncLabel();
          close();
          button.focus();
        });
        list.appendChild(optionButton);
      });
      syncLabel();
    }

    rebuildOptions();

    button.addEventListener("click", () => {
      const shouldOpen = !custom.classList.contains("is-open");
      document.querySelectorAll(".custom-select.is-open").forEach((node) => {
        if (node !== custom) {
          node.classList.remove("is-open");
          node.querySelector(".custom-select__button")?.setAttribute("aria-expanded", "false");
        }
      });
      custom.classList.toggle("is-open", shouldOpen);
      button.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    });

    button.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        close();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        custom.classList.add("is-open");
        button.setAttribute("aria-expanded", "true");
        const options = Array.from(list.querySelectorAll(".custom-select__option:not(:disabled)"));
        const selected = options.find((option) => option.getAttribute("aria-selected") === "true");
        (selected || options[0])?.focus();
      }
    });

    list.addEventListener("keydown", (event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End", "Escape"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Escape") {
        close();
        button.focus();
        return;
      }
      const options = Array.from(list.querySelectorAll(".custom-select__option:not(:disabled)"));
      const current = Math.max(0, options.indexOf(document.activeElement));
      const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 :
        (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
      options[next]?.focus();
    });

    select.addEventListener("change", syncLabel);
    select.addEventListener("custom-select:refresh", rebuildOptions);
  });

  if (window.customSelectDismissMounted) return;
  window.customSelectDismissMounted = true;
  document.addEventListener("click", (event) => {
    if (event.target.closest(".custom-select")) return;
    document.querySelectorAll(".custom-select.is-open").forEach((node) => {
      node.classList.remove("is-open");
      node.querySelector(".custom-select__button")?.setAttribute("aria-expanded", "false");
    });
  });
}

function mountMobileMenu() {
  const header = document.querySelector(".site-header");
  const toggle = header?.querySelector("[data-mobile-menu-toggle]");
  if (!header || !toggle) return;
  const setOpen = (open) => {
    header.classList.toggle("is-mobile-menu-open", open);
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.querySelector(".sr-only").textContent = open ? "Close menu" : "Open menu";
  };
  toggle.addEventListener("click", () => setOpen(!header.classList.contains("is-mobile-menu-open")));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setOpen(false);
  });
}

function mountFilterAccordions() {
  document.querySelectorAll("[data-filter-accordion]").forEach((accordion) => {
    const trigger = accordion.querySelector("[data-filter-accordion-trigger]");
    if (!trigger || trigger.dataset.accordionMounted === "true") return;

    trigger.dataset.accordionMounted = "true";
    trigger.addEventListener("click", () => {
      const isOpen = accordion.classList.toggle("is-open");
      trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    });
  });
}

function mountFilterSearches() {
  document.querySelectorAll("[data-filter-search]").forEach((input) => {
    if (input.dataset.filterSearchMounted === "true") return;
    input.dataset.filterSearchMounted = "true";
    const accordion = input.closest("[data-filter-accordion]");
    const list = accordion?.querySelector(".custom-select__list");
    if (!list) return;

    const filterOptions = () => {
      const query = input.value.trim().toLowerCase();
      list.querySelectorAll(".custom-select__option").forEach((option) => {
        const label = option.textContent.trim().toLowerCase();
        option.hidden = Boolean(query) && !label.includes(query);
      });
    };

    input.addEventListener("input", filterOptions);
    filterOptions();
  });
}

function mountCatalogFilterDrawer() {
  const root = document.querySelector("[data-filter-drawer-root]");
  const drawer = root?.querySelector("[data-filter-drawer]");
  const backdrop = root?.querySelector("[data-filter-drawer-close].catalog-filter-backdrop");
  const openButton = root?.querySelector("[data-filter-drawer-open]");
  if (!root || !drawer || !openButton) return;

  function updateDrawerOffset() {
    const header = document.querySelector(".site-header");
    const nav = header?.querySelector(".nav-wrap");
    const headerHeight = nav?.offsetHeight || header?.offsetHeight || 0;
    document.documentElement.style.setProperty("--catalog-filter-top", `${Math.max(0, headerHeight + 12)}px`);
  }

  function setOpen(isOpen) {
    if (isOpen) {
      updateDrawerOffset();
    }
    root.classList.toggle("is-filter-open", isOpen);
    drawer.setAttribute("aria-hidden", isOpen ? "false" : "true");
    openButton.setAttribute("aria-expanded", isOpen ? "true" : "false");
    if (backdrop) {
      backdrop.hidden = !isOpen;
    }
    document.body.classList.toggle("catalog-filter-open", isOpen);
  }

  openButton.addEventListener("click", () => setOpen(true));
  root.querySelectorAll("[data-filter-drawer-close]").forEach((button) => {
    button.addEventListener("click", () => setOpen(false));
  });
  drawer.querySelector("form")?.addEventListener("submit", () => setOpen(false));
  window.addEventListener("resize", () => {
    if (root.classList.contains("is-filter-open")) {
      updateDrawerOffset();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && root.classList.contains("is-filter-open")) {
      setOpen(false);
      openButton.focus();
    }
  });
}

function mountSmartHeader() {
  const header = document.querySelector(".site-header");
  if (!header) return;

  const nav = header.querySelector(".nav-wrap");
  let ticking = false;
  let isCondensed = false;
  let isNavHidden = false;
  let lastScrollY = Math.max(window.scrollY, 0);
  let scrollIntent = 0;
  let lastDirection = 0;

  function setHeaderMetrics() {
    const navHeight = nav?.offsetHeight || header.offsetHeight || 0;
    const visibleHeight = isNavHidden ? 0 : navHeight;
    const visibleOffset = isNavHidden ? 16 : Math.max(72, visibleHeight + 12);
    document.documentElement.style.setProperty("--site-header-height", `${visibleHeight}px`);
    document.documentElement.style.setProperty("--filter-sticky-top", `${visibleOffset}px`);
  }

  function setCondensed(nextCondensed) {
    if (isCondensed === nextCondensed) return;
    isCondensed = nextCondensed;
    header.classList.toggle("is-condensed", isCondensed);
    setHeaderMetrics();
  }

  function setNavHidden(nextHidden) {
    if (isNavHidden === nextHidden) return;
    isNavHidden = nextHidden;
    header.classList.toggle("is-nav-hidden", isNavHidden);
    if (isNavHidden) {
      header.querySelectorAll(".nav-item--mega.is-open").forEach((item) => {
        item.classList.remove("is-open");
      });
    }
    setHeaderMetrics();
  }

  function update() {
    ticking = false;
    const currentScrollY = Math.max(window.scrollY, 0);
    const scrollDelta = currentScrollY - lastScrollY;
    const absDelta = Math.abs(scrollDelta);
    const isSearchOpen = document.body.classList.contains("search-focus-open");
    const hasOpenMenu = Boolean(header.querySelector(".nav-item--mega.is-open"));

    header.classList.toggle("is-glass", currentScrollY > 12);

    if (isSearchOpen || hasOpenMenu || currentScrollY <= 12) {
      scrollIntent = 0;
      lastDirection = 0;
      setCondensed(false);
      setNavHidden(false);
      lastScrollY = currentScrollY;
      return;
    }

    setCondensed(currentScrollY > 42);

    if (absDelta >= 3) {
      const direction = scrollDelta > 0 ? 1 : -1;
      if (direction !== lastDirection) {
        scrollIntent = 0;
        lastDirection = direction;
      }
      scrollIntent += absDelta;

      if (direction > 0 && currentScrollY > 140 && scrollIntent > 42) {
        setNavHidden(true);
        scrollIntent = 0;
      } else if (direction < 0 && scrollIntent > 34) {
        setNavHidden(false);
        scrollIntent = 0;
      }
    }

    lastScrollY = currentScrollY;
  }

  function requestUpdate() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(update);
  }

  setHeaderMetrics();
  update();
  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", () => {
    setHeaderMetrics();
    requestUpdate();
  });
}

function mountSearchFocus() {
  const overlay = document.querySelector("[data-search-focus]");
  const overlayForm = overlay?.querySelector("[data-search-focus-form]");
  const overlayInput = overlay?.querySelector("[data-search-focus-input]");
  const closeButton = overlay?.querySelector("[data-search-close]");
  const trigger = document.querySelector("[data-search-trigger]");
  const triggerInput = trigger?.querySelector("[data-search-preview]");
  if (!overlay || !overlayForm || !overlayInput || !trigger) return;

  let closeTimer = null;

  function openSearch(seedValue = "") {
    window.clearTimeout(closeTimer);
    overlay.hidden = false;
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("search-focus-open");

    const cleanSeed = String(seedValue || "").trim();
    overlayInput.value = cleanSeed;

    window.requestAnimationFrame(() => {
      overlay.classList.add("is-open");
      window.setTimeout(() => {
        overlayInput.focus();
        overlayInput.select();
      }, 120);
    });
  }

  function closeSearch() {
    overlay.classList.remove("is-open");
    document.body.classList.remove("search-focus-open");
    overlay.setAttribute("aria-hidden", "true");
    closeTimer = window.setTimeout(() => {
      overlay.hidden = true;
      overlayInput.value = "";
      triggerInput?.blur();
    }, 180);
  }

  function openFromTrigger(event) {
    event.preventDefault();
    openSearch(triggerInput?.value || "");
  }

  trigger.addEventListener("submit", openFromTrigger);
  trigger.addEventListener("click", openFromTrigger);
  trigger.addEventListener("pointerdown", () => trigger.classList.add("is-peeking"));
  trigger.addEventListener("pointerenter", () => trigger.classList.add("is-peeking"));
  trigger.addEventListener("pointerleave", () => trigger.classList.remove("is-peeking"));
  trigger.addEventListener("focusout", () => trigger.classList.remove("is-peeking"));

  closeButton?.addEventListener("click", closeSearch);

  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      closeSearch();
    }
  });

  overlayForm.addEventListener("submit", (event) => {
    const query = overlayInput.value.trim();
    if (!query) {
      event.preventDefault();
      closeSearch();
      return;
    }
    overlayInput.value = query;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && overlay.classList.contains("is-open")) {
      closeSearch();
    }
  });
}

function mountMegaMenus() {
  const megaItems = document.querySelectorAll(".nav-item--mega");
  if (!megaItems.length) return;

  megaItems.forEach((item) => {
    const trigger = item.querySelector(":scope > .nav-link");
    if (!trigger || trigger.dataset.megaMounted === "true") return;
    trigger.dataset.megaMounted = "true";
    trigger.addEventListener("click", (event) => {
      if (window.matchMedia("(max-width: 760px)").matches) return;
      if (item.classList.contains("is-open")) return;
      event.preventDefault();
      megaItems.forEach((node) => node.classList.remove("is-open"));
      item.classList.add("is-open");
    });
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest(".nav-item--mega")) return;
    megaItems.forEach((item) => item.classList.remove("is-open"));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    megaItems.forEach((item) => item.classList.remove("is-open"));
  });
}

function mountAutoSubmitControls() {
  document.querySelectorAll("[data-auto-submit]").forEach((control) => {
    control.addEventListener("change", () => {
      control.form?.requestSubmit();
    });
  });
}

function mountHeroCarousel() {
  const root = document.querySelector("[data-hero-carousel]");
  if (!root) return;

  const frames = Array.from(root.querySelectorAll(".hero__media-frame"));
  if (frames.length < 2 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  let activeIndex = Math.max(0, frames.findIndex((frame) => frame.classList.contains("is-active")));

  window.setInterval(() => {
    if (document.hidden) return;
    frames[activeIndex].classList.remove("is-active");
    activeIndex = (activeIndex + 1) % frames.length;
    frames[activeIndex].classList.add("is-active");
  }, 6500);
}

function mountAnimationLab() {
  const lab = document.querySelector("[data-animation-lab]");
  if (!lab) return;

  const film = lab.querySelector(".animation-lab__film");
  if (!film) return;

  const splitVisibility = 0.8;
  const restoreTopRatio = 0.18;
  let lastScrollY = window.scrollY;
  let isSplit = lab.classList.contains("is-split");
  let ticking = false;

  function getVerticalVisibleRatio(element) {
    const rect = element.getBoundingClientRect();
    const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
    return visibleHeight / Math.max(1, rect.height);
  }

  function updateAnimationState() {
    ticking = false;
    const currentScrollY = window.scrollY;
    const isScrollingDown = currentScrollY > lastScrollY + 1;
    const isScrollingUp = currentScrollY < lastScrollY - 1;
    const labRect = lab.getBoundingClientRect();
    const isInView = labRect.top < window.innerHeight && labRect.bottom > 0;
    const filmRect = film.getBoundingClientRect();
    const visibleRatio = getVerticalVisibleRatio(film);
    const isComposedInViewport = filmRect.top <= window.innerHeight * 0.38;

    if (currentScrollY <= 1) {
      isSplit = false;
    } else if (isInView && isScrollingDown && isComposedInViewport && visibleRatio >= splitVisibility) {
      isSplit = true;
    } else if (isScrollingUp && labRect.top > window.innerHeight * restoreTopRatio) {
      isSplit = false;
    }

    lab.classList.toggle("is-split", isSplit);
    lastScrollY = currentScrollY;
  }

  function requestUpdate() {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(updateAnimationState);
  }

  updateAnimationState();
  window.addEventListener("scroll", requestUpdate, { passive: true });
  window.addEventListener("resize", requestUpdate);
}

document.addEventListener("click", (event) => {
  const wishlistToggle = event.target.closest("[data-wishlist-toggle]");
  if (wishlistToggle) {
    toggleWishlist(wishlistToggle.dataset.wishlistId, wishlistToggle.dataset.itemLabel || "Fragrance");
    return;
  }

  const wishlistButton = event.target.closest("[data-wishlist-trigger]");
  if (wishlistButton) {
    showToast("Wishlist is coming soon.");
    return;
  }

  const addButton = event.target.closest("[data-add-to-cart]");
  if (addButton) {
    addToCart(Number(addButton.dataset.variantId), addButton.dataset.itemLabel || "Item");
    return;
  }

  const changeButton = event.target.closest("[data-cart-change]");
  if (changeButton) {
    updateQuantity(Number(changeButton.dataset.variantId), Number(changeButton.dataset.cartChange));
    mountCartPage();
    return;
  }

  const removeButton = event.target.closest("[data-cart-remove]");
  if (removeButton) {
    removeItem(Number(removeButton.dataset.variantId));
    mountCartPage();
  }
});

updateCartCount();
updateWishlistButtons();
mountThemeToggle();
mountMobileMenu();
mountReveals();
mountGatewayTilt();
mountConcierge();
mountCartPage();
mountCheckoutPage();
mountCustomSelects();
mountFilterAccordions();
mountFilterSearches();
mountCatalogFilterDrawer();
mountSearchFocus();
mountMegaMenus();
mountAutoSubmitControls();
mountHeroCarousel();
mountAnimationLab();
mountSmartHeader();
