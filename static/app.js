const CART_KEY = "the-scentist-cart-v1";
const THEME_KEY = "the-scentist-theme";
const SHIPPING_FEE = 350;
const FREE_SHIPPING_THRESHOLD = 12500;

const money = (value) =>
  new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value || 0);

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
  return `<img class="${className}" src="${imageUrl}" alt="${item.brand} ${item.name}" loading="lazy" />`;
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
              <strong>${item.brand} ${item.name}</strong>
              <p>${item.size_label} - ${item.sale_type.replace(/_/g, " ")}</p>
              <div class="qty-control">
                <button type="button" data-cart-change="-1" data-variant-id="${item.variant_id}">-</button>
                <span>${item.quantity}</span>
                <button type="button" data-cart-change="1" data-variant-id="${item.variant_id}">+</button>
              </div>
            </div>
            <div class="cart-item__price">
              <strong>${money(item.line_total)}</strong>
              <div>${item.quantity} x ${money(item.price_inr)}</div>
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
            <div>
              <strong>${item.brand} ${item.name}</strong>
              <p>${item.size_label} - Qty ${item.quantity}</p>
            </div>
            <strong>${money(item.line_total)}</strong>
          </div>
        `
      )
      .join("");
  }

  if (!form || !submitButton || !methodSelect) return;

  const emailInput = form.querySelector('input[name="email"]');
  const phoneInput = form.querySelector('input[name="phone"]');
  const addressInput = form.querySelector('input[name="shipping_line1"]');
  const cityInput = form.querySelector('input[name="city"]');
  const stateInput = form.querySelector('input[name="state"]');
  const postalInput = form.querySelector('input[name="postal_code"]');
  const countryInput = form.querySelector('input[name="country"]');

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

  function runCheckoutValidation() {
    const checks = [
      validateRequiredText(addressInput, "Address", 8),
      validateRequiredText(cityInput, "City"),
      validateRequiredText(stateInput, "State"),
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
    [cityInput, () => validateRequiredText(cityInput, "City")],
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
          submitButton.disabled = false;
        });

        razorpayInstance.open();
        return;
      }

      const result = await fetchJson("/api/orders", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      writeCart([]);
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

document.addEventListener("click", (event) => {
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
mountThemeToggle();
mountReveals();
mountGatewayTilt();
mountCartPage();
mountCheckoutPage();
