/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        plane: '#060B14',
        surface: { DEFAULT: '#0D1420', 2: '#111827', 3: '#1A2333' },
        ink: {
          primary: '#E2EAF4',
          secondary: '#8BA0BB',
          muted: '#4A6080',
        },
        chrome: { grid: '#1E2D42', border2: '#253448' },
        status: {
          good: '#10B981',
          warning: '#F59E0B',
          critical: '#EF4444',
          info: '#00D4FF',
          purple: '#A78BFA',
        },
      },
      fontFamily: {
        sans: ["'Space Grotesk'", 'sans-serif'],
        mono: ["'JetBrains Mono'", 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 2s cubic-bezier(0.4,0,0.6,1) infinite',
      },
    },
  },
  plugins: [],
}
