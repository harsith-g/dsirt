const form = document.getElementById("loginForm");
const errorBox = document.getElementById("loginError");
const submitBtn = document.getElementById("loginSubmitBtn");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("visible");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.remove("visible");
  submitBtn.disabled = true;
  submitBtn.textContent = "Signing in…";

  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
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
