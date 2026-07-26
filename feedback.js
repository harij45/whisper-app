const feedbackInput = document.getElementById("feedbackInput");
const feedbackButton = document.getElementById("submitFeedbackBtn");
const feedbackStatus = document.getElementById("feedbackStatus");

async function submitFeedback() {
    const text = feedbackInput.value.trim();
    if (!text) {
        feedbackStatus.textContent = "Please enter feedback.";
        feedbackStatus.classList.add("error");
        return;
    }

    feedbackButton.disabled = true;
    feedbackStatus.textContent = "Sending…";
    feedbackStatus.classList.remove("error");

    try {
        const response = await fetch("/api/feedback", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
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
        feedbackButton.disabled = false;
    }
}

feedbackButton.addEventListener("click", submitFeedback);
