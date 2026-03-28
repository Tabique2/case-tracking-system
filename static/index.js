document.addEventListener('DOMContentLoaded', () => {
    const inputs = [
        document.querySelector('input[name="complainant"]'),
        document.querySelector('input[name="respondent"]')
    ];

    inputs.forEach(input => {
        if (!input) return;

        let maskedValue = '';        // what shows in input
        let originalValue = '';      // full actual name
        let timeoutIds = [];         // timeouts for masking letters

        input.addEventListener('input', (e) => {
            const newChar = e.target.value.slice(-1); // last typed character

            // Handle space
            if (newChar === ' ') {
                maskedValue += ' ';
                originalValue += ' ';
                e.target.value = maskedValue;
                return;
            }

            originalValue += newChar;

            // Determine if letter should be masked or visible
            if (originalValue.replace(/ /g, '').length <= 3) {
                // First 3 letters visible
                maskedValue += newChar;
                e.target.value = maskedValue;
            } else {
                // Letters after 3 → show briefly, then mask after 0.3s
                maskedValue += newChar;
                e.target.value = maskedValue;

                // Clear previous timeout for last char
                if (timeoutIds.length > 0) {
                    clearTimeout(timeoutIds[timeoutIds.length - 1]);
                }

                const id = setTimeout(() => {
                    maskedValue = maskedValue.slice(0, -1) + '*';
                    e.target.value = maskedValue;
                }, 300);

                timeoutIds.push(id);
            }
        });

        // Handle backspace
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace') {
                // Remove last character from original and masked
                originalValue = originalValue.slice(0, -1);
                maskedValue = maskedValue.slice(0, -1);

                // Clear last timeout
                if (timeoutIds.length > 0) {
                    clearTimeout(timeoutIds.pop());
                }

                e.target.value = maskedValue;
            }
        });
    });
});

document.addEventListener('DOMContentLoaded', () => {
    const caseNumberInput = document.querySelector('input[name="case_number"]');

    if (caseNumberInput) {
        caseNumberInput.addEventListener('input', (e) => {
            // Remove any letters typed
            e.target.value = e.target.value.replace(/[A-Za-z]/g, '');
        });
    }
});