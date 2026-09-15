/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: { DEFAULT: '#005EB8', dark: '#003F7F', light: '#E8F1FB' },
      },
    },
  },
  plugins: [],
};
