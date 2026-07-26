const feedbackInput = document.getElementById("feedbackInput");
const feedbackButton = document.getElementById("submitFeedbackBtn");
const feedbackStatus = document.getElementById("feedbackStatus");
const turnstileStatus = document.getElementById("turnstileStatus");
let turnstileToken = "";
let turnstileWidgetId = null;

async function initializeTurnstile() {
    try {
        const response = await fetch("/api/turnstile-config");
        const config = await response.json();
        if (!response.ok) {
            throw new Error(config.error || "Spam protection could not load.");
        }

        turnstileWidgetId = window.turnstile.render("#turnstileWidget", {
            sitekey: config.siteKey,
            theme: "dark",
            size: "flexible",
            action: "feedback",
            callback(token) {
                turnstileToken = token;
                feedbackButton.disabled = false;
                turnstileStatus.textContent = "Verification complete.";
                turnstileStatus.classList.remove("error");
            },
            "expired-callback"() {
                turnstileToken = "";
                feedbackButton.disabled = true;
                turnstileStatus.textContent = "Verification expired. Please try again.";
                turnstileStatus.classList.add("error");
            },
            "error-callback"() {
                turnstileToken = "";
                feedbackButton.disabled = true;
                turnstileStatus.textContent =
                    "Spam protection could not load. Please refresh the page.";
                turnstileStatus.classList.add("error");
            }
        });
    } catch (error) {
        feedbackButton.disabled = true;
        turnstileStatus.textContent = error.message;
        turnstileStatus.classList.add("error");
    }
}

window.initializeTurnstile = initializeTurnstile;

async function submitFeedback() {
    const text = feedbackInput.value.trim();
    if (!text) {
        feedbackStatus.textContent = "Please enter feedback.";
        feedbackStatus.classList.add("error");
        return;
    }
    if (!turnstileToken) {
        turnstileStatus.textContent = "Please complete the spam check.";
        turnstileStatus.classList.add("error");
        return;
    }

    feedbackButton.disabled = true;
    feedbackStatus.textContent = "Sending…";
    feedbackStatus.classList.remove("error");

    try {
        const response = await fetch("/api/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, turnstileToken })
        });
        const body = await response.json();
        if (!response.ok) {
            throw new Error(body.error || "Could not send feedback.");
        }
        feedbackInput.value = "";
        feedbackStatus.textContent = "Thank you for your feedback.";
    } catch (error) {
        feedbackStatus.textContent = error.message;
        feedbackStatus.classList.add("error");
    } finally {
        turnstileToken = "";
        if (turnstileWidgetId !== null && window.turnstile) {
            window.turnstile.reset(turnstileWidgetId);
            turnstileStatus.textContent = "Running a new verification…";
            turnstileStatus.classList.remove("error");
        }
    }
}

feedbackButton.addEventListener("click", submitFeedback);
