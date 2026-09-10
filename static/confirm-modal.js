/**
 * Planet's replacement for the browser's native confirm() dialog, styled to
 * match the rest of the app instead of looking like a generic OS popup.
 *
 * Usage inside an async function:
 *     const sure = await planetConfirm("Delete this course?");
 *     if (!sure) return;
 *
 * Usage on a <form> that used to have onsubmit="return confirm('...')":
 *     planetConfirmForm(formElement, "Delete this course?");
 * (call this once when the page loads; it wires up the submit listener)
 */
function planetConfirm(message, options = {}) {
    return new Promise((resolve) => {
        const overlay = document.getElementById("planet-confirm-overlay");
        const titleEl = document.getElementById("planet-confirm-title");
        const messageEl = document.getElementById("planet-confirm-message");
        const okBtn = document.getElementById("planet-confirm-ok");
        const cancelBtn = document.getElementById("planet-confirm-cancel");

        if (!overlay || !messageEl || !okBtn || !cancelBtn) {
            // Fallback in case the modal partial wasn't included on this page.
            resolve(window.confirm(message));
            return;
        }

        titleEl.textContent = options.title || "Are you sure?";
        messageEl.textContent = message;
        okBtn.textContent = options.confirmLabel || "Delete";
        okBtn.classList.toggle("is-danger", options.danger !== false);
        overlay.hidden = false;
        okBtn.focus();

        function cleanup(result) {
            overlay.hidden = true;
            okBtn.removeEventListener("click", onOk);
            cancelBtn.removeEventListener("click", onCancel);
            overlay.removeEventListener("mousedown", onOverlayClick);
            document.removeEventListener("keydown", onKeydown);
            resolve(result);
        }

        function onOk() { cleanup(true); }
        function onCancel() { cleanup(false); }
        function onOverlayClick(event) {
            if (event.target === overlay) cleanup(false);
        }
        function onKeydown(event) {
            if (event.key === "Escape") cleanup(false);
        }

        okBtn.addEventListener("click", onOk);
        cancelBtn.addEventListener("click", onCancel);
        overlay.addEventListener("mousedown", onOverlayClick);
        document.addEventListener("keydown", onKeydown);
    });
}

/**
 * Wires a form's submit event to show the custom confirmation modal instead
 * of a native confirm(), then submits for real once the user confirms.
 */
function planetConfirmForm(form, message, options = {}) {
    if (!form) return;
    form.addEventListener("submit", async (event) => {
        if (form.dataset.planetConfirmed === "true") {
            form.dataset.planetConfirmed = "false";
            return;
        }
        event.preventDefault();
        const sure = await planetConfirm(message, options);
        if (sure) {
            form.dataset.planetConfirmed = "true";
            if (form.requestSubmit) {
                form.requestSubmit();
            } else {
                form.submit();
            }
        }
    });
}
