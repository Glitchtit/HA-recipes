/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          orange:      'var(--brand-orange)',
          'orange-600':'var(--brand-orange-600)',
          'orange-400':'var(--brand-orange-400)',
          'orange-300':'var(--brand-orange-300)',
          'orange-100':'var(--brand-orange-100)',
          cobalt:      'var(--brand-cobalt)',
          'cobalt-600':'var(--brand-cobalt-600)',
          'cobalt-400':'var(--brand-cobalt-400)',
          'cobalt-300':'var(--brand-cobalt-300)',
          'cobalt-100':'var(--brand-cobalt-100)',
        },
        semantic: {
          success: 'var(--success)',
          warning: 'var(--warning)',
          danger:  'var(--danger)',
          info:    'var(--info)',
        },
        'xp-gold':      'var(--xp-gold)',
        'xp-gold-soft': 'var(--xp-gold-soft)',
      },
      fontFamily: {
        sans:    ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        display: ['Space Grotesk', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        body:    ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono:    ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        xl:   '12px',
        '2xl':'16px',
      },
    },
  },
  plugins: [],
};
