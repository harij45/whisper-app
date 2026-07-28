async function signIn(event) {
    event.preventDefault();
    const button = document.getElementById("adminLoginBtn");
    const status = document.getElementById("adminLoginStatus");
    const username = document.getElementById("adminUsername").value.trim();
    const password = document.getElementById("adminPassword").value;

    button.disabled = true;
    status.textContent = "Signing in…";
    status.classList.remove("error");

    try {
        const response = await fetch("/api/admin/login", {
            method: "POST",
            credentials: "same-origin",
            headers: {
                "Content-Type": "application/json",
                "X-Admin-Action": "Whisper-Admin"
            },
            body: JSON.stringify({ username, password })
        });

        if (!response.ok) {
            const body = await response.json();
            throw new Error(body.error || "Sign in failed.");
        }

        window.location.replace("/admin");
    } catch (error) {
        status.textContent = error.message;
        status.classList.add("error");
        button.disabled = false;
    }
}

document.getElementById("adminLoginForm").addEventListener("submit", signIn);
