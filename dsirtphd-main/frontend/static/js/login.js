const form = document.getElementById("loginForm");
const errorBox = document.getElementById("loginError");
const submitBtn = document.getElementById("loginSubmitBtn");
const passwordInput = document.getElementById("password");
const emailInput = document.getElementById("email");
const togglePasswordBtn = document.getElementById("togglePasswordBtn");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("visible");
}

if (togglePasswordBtn && passwordInput) {
  togglePasswordBtn.addEventListener("click", () => {
    if (passwordInput.type === "password") {
      passwordInput.type = "text";
      togglePasswordBtn.textContent = "Hide password";
    } else {
      passwordInput.type = "password";
      togglePasswordBtn.textContent = "Show password";
    }
  });
}

// Only show demo helper if explicitly requested via ?demo=1 in URL (hidden by default for candidates and in production)
const urlParams = new URLSearchParams(window.location.search);
if (urlParams.get("demo") === "1" || urlParams.get("demo") === "true") {
  const demoBox = document.getElementById("demoLoginsBox");
  if (demoBox) demoBox.style.display = "block";
}

document.querySelectorAll(".quick-fill-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const email = btn.getAttribute("data-email");
    const pass = btn.getAttribute("data-pass");
    if (emailInput && email) emailInput.value = email;
    if (passwordInput && pass) passwordInput.value = pass;
    errorBox.classList.remove("visible");
  });
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.remove("visible");
  submitBtn.disabled = true;
  submitBtn.textContent = "Signing in…";

  const email = emailInput.value.trim();
  const password = passwordInput.value.trim();

  try {
    let res;
    try {
      res = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    } catch (netErr) {
      // In case of momentary exam-hall Wi-Fi congestion or socket collision, retry once after 400ms
      await new Promise((resolve) => setTimeout(resolve, 400));
      res = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
    }

    const data = await res.json();

    if (!res.ok) {
      showError(data.detail || "Sign in failed.");
      submitBtn.disabled = false;
      submitBtn.textContent = "Sign in";
      return;
    }

    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("role", data.role);
    localStorage.setItem("full_name", data.full_name);

    // Route by role — no reset-password redirect for candidates
    // (candidates use the universal password dsirt@123)
    if (data.must_reset_password && data.role !== "candidate") {
      window.location.href = "/reset-password";
    } else if (data.role === "admin") {
      window.location.href = "/admin";
    } else if (data.role === "setter") {
      window.location.href = "/setter";
    } else {
      window.location.href = "/candidate";
    }
  } catch (err) {
    showError("Could not reach the server. Try again.");
    submitBtn.disabled = false;
    submitBtn.textContent = "Sign in";
  }
});
