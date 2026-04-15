/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          DEFAULT: '#007857',
          dark: '#005c43',
          soft: 'rgba(0, 120, 87, 0.08)'
        }
      }
    },
  },
  plugins: [],
}
