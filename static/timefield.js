/* 24-hour time field you can just type digits into.
 *
 *   "232"  -> 23:2   (23 stays the hour, the 2 starts the minutes)
 *   "2325" -> 23:25
 *   "9"    -> 09:    (can't be a 2-digit hour, so it's padded and we move to minutes)
 *   "25"   -> 02:5   (25 isn't an hour, so 2 is the hour and 5 starts the minutes)
 *   "17"   -> 17:    "1" + blur -> 01:00
 *
 * Backspace removes one digit at a time (and steps back over the colon). Typing always appends at
 * the end, a finished time starts over on the next digit, and non-digits are ignored.
 */
(function (root) {
  "use strict";

  // Turns the digits typed so far into { hh, mm } (either may be partial or empty).
  function parseDigits(raw) {
    const digits = String(raw).replace(/\D/g, "");
    let hh = "", mm = "", i = 0;
    if (i < digits.length) {
      const a = digits[i++];
      if (a > "2") hh = "0" + a;                       // 3-9 can only be the ones digit
      else if (i < digits.length) {
        const b = digits[i++];
        if (a === "2" && b > "3") { hh = "02"; i--; }  // 24-29: hour 02, b starts the minutes
        else hh = a + b;
      } else hh = a;                                   // one digit so far, waiting for the next
    }
    if (hh.length === 2 && i < digits.length) {
      const c = digits[i++];
      if (c > "5") mm = "0" + c;                       // 6-9 can only be the ones digit
      else if (i < digits.length) mm = c + digits[i++];
      else mm = c;
    }
    return { hh, mm };
  }

  function format(raw) {
    const { hh, mm } = parseDigits(raw);
    return hh.length === 2 ? hh + ":" + mm : hh;
  }

  // "HH:MM" if the text can be completed into a valid time, else null (empty).
  function complete(text) {
    const { hh, mm } = parseDigits(text);
    if (!hh) return null;
    const h = hh.length === 1 ? "0" + hh : hh;
    return h + ":" + (mm.length === 2 ? mm : mm.padEnd(2, "0"));
  }

  const digitsOf = (s) => String(s).replace(/\D/g, "");

  // ---- pure helpers (unit-tested) ----
  // Typing always appends at the end, wherever the caret is. A finished time (HH:MM) or a
  // fully-selected field starts over, so you can just click in and type a new time.
  function typeDigit(current, digit, selectedAll) {
    const fresh = selectedAll || current.length === 5;
    return format((fresh ? "" : digitsOf(current)) + digit);
  }
  // Backspace removes one digit (stepping back over the colon); with everything selected it clears.
  function backspace(current, selectedAll) {
    return selectedAll ? "" : format(digitsOf(current).slice(0, -1));
  }
  // Fallback for input that can't be intercepted (e.g. IME composition): re-format what's there.
  function handleInput(rawValue, prevDigits, isDelete) {
    let d = digitsOf(rawValue);
    if (isDelete && d === prevDigits) d = d.slice(0, -1);
    return format(d);
  }

  function attach(input) {
    input.type = "text";
    input.inputMode = "numeric";
    input.autocomplete = "off";
    input.maxLength = 5;
    input.placeholder = "HH:MM";
    input.classList.add("time-input");
    input._tfPrev = digitsOf(input.value);
    const show = (text) => { input.value = text; input._tfPrev = digitsOf(text); };
    const allSelected = () => input.value.length > 0 && input.selectionStart === 0 && input.selectionEnd === input.value.length;

    input.addEventListener("beforeinput", (e) => {
      const t = e.inputType || "";
      if (t === "insertText") {
        e.preventDefault();                                   // only digits get through
        if (/^\d$/.test(e.data || "")) show(typeDigit(input.value, e.data, allSelected()));
      } else if (t.startsWith("delete")) {
        e.preventDefault();
        show(backspace(input.value, allSelected()));
      } else if (t === "insertFromPaste" || t === "insertFromDrop") {
        e.preventDefault();
        const text = (e.dataTransfer && e.dataTransfer.getData("text")) || e.data || "";
        show(format(digitsOf(text)));
      }
    });
    input.addEventListener("input", (e) => {                  // fallback path (composition etc.)
      const isDelete = !!(e.inputType && e.inputType.startsWith("delete"));
      show(handleInput(input.value, input._tfPrev, isDelete));
    });
    input.addEventListener("blur", () => show(complete(input.value) || ""));
    input.addEventListener("focus", () => input.select());   // typing replaces the whole time
    return input;
  }

  const api = {
    attach,
    parseDigits, format, complete, handleInput, typeDigit, backspace,
    get: (input) => complete(input.value),                    // "HH:MM" or null
    set: (input, value) => { input.value = value || ""; input._tfPrev = digitsOf(input.value); },
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TimeField = api;
})(typeof window !== "undefined" ? window : this);
