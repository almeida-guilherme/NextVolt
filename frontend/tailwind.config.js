/**
 * Tokens mirror `src/theme.js` (the single source of truth for chart colors).
 * Palette validated for the dark surface #1a1a19 — lightness band, chroma
 * floor, CVD separation, normal-vision floor and contrast all pass.
 */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        plane: '#0d0d0d',
        surface: '#1a1a19',
        raised: '#212120',
        grid: '#2c2c2a',
        baseline: '#383835',
        ink: {
          primary: '#ffffff',
          secondary: '#c3c2b7',
          muted: '#898781',
        },
        series: {
          1: '#3987e5',
          2: '#d95926',
          3: '#199e70',
        },
        status: {
          good: '#0ca30c',
          warning: '#fab219',
          serious: '#ec835a',
          critical: '#d03b3b',
        },
        track: '#184f95',
      },
      fontFamily: {
        sans: ['system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
      },
      boxShadow: {
        hairline: 'inset 0 0 0 1px rgba(255,255,255,0.10)',
      },
    },
  },
  plugins: [],
}
