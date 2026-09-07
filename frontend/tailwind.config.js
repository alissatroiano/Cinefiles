/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./*.html'],
  theme: {
    extend: {
      colors: {
        /* ── Brand palette ───────────────────────────────────────── */
        brand: {
          DEFAULT: '#7c3aed', /* violet-700 — primary action colour */
          light:   '#a78bfa', /* violet-400 — accent / links        */
          dark:    '#5b21b6', /* violet-800 — hover states          */
        },
        /* ── Dashboard surface colours ───────────────────────────── */
        surface: {
          DEFAULT: '#111827', /* gray-900  — card backgrounds        */
          deep:    '#030712', /* gray-950  — page background         */
          raised:  '#1f2937', /* gray-800  — elevated panels         */
        },
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        '2xl': '1rem',
        '3xl': '1.5rem',
      },
    },
  },
  plugins: [],
};
