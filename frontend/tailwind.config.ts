import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: '#ffffff',
          secondary: '#fafafa',
          elevated: '#ffffff',
        },
        border: {
          DEFAULT: '#e5e5e7',
        },
        text: {
          primary: '#1a1a1a',
          secondary: '#6b6b6f',
          tertiary: '#9b9ba1',
        },
        accent: {
          DEFAULT: '#0066ff',
          hover: '#0052cc',
        },
        success: '#34c759',
        warning: '#ff9500',
        danger: '#ff3b30',
        cefr: {
          a2: '#1e8a3e',
          'a2-bg': '#d1f5dc',
          b1: '#0052cc',
          'b1-bg': '#d6e4ff',
          b2: '#7b30ae',
          'b2-bg': '#edd9f7',
        },
      },
      borderRadius: {
        sm: '6px',
        md: '10px',
        lg: '16px',
        xl: '20px',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      boxShadow: {
        sm: '0 1px 2px rgba(0,0,0,0.04)',
        md: '0 4px 12px rgba(0,0,0,0.08)',
        lg: '0 12px 32px rgba(0,0,0,0.12)',
      },
      transitionTimingFunction: {
        'ease-apple': 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      },
      transitionDuration: {
        fast: '150ms',
        base: '240ms',
        slow: '400ms',
      },
    },
  },
} satisfies Config
