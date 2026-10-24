const token = localStorage.getItem("access_token");
if (!token) {
  window.location.href = "/login";
}

const form = document.getElementById("resetForm");
const errorBox = document.getElementById("resetError");

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.classList.add("visible");
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errorBox.classList.remove("visible");

  const newPassword = document.getElementById("newPassword").value;
  const confirmPassword = document.getElementById("confirmPassword").value;

  if (newPassword !== confirmPassword) {
    showError("Passwords don't match.");
    return;
  }

  try {
    const res = await fetch("/auth/reset-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ new_password: newPassword }),
    });
    const data = await res.json();

    if (!res.ok) {
      showError(data.detail || "Could not reset password.");
      return;
    }

    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("role", data.role);

    if (data.role === "admin") {
      window.location.href = "/admin";
    } else if (data.role === "setter") {
      window.location.href = "/setter";
    } else {
      window.location.href = "/exam";
    }
  } catch (err) {
    showError("Could not reach the server. Try again.");
  }
});
