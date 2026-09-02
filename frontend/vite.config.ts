import { defineConfig } from 'vite'
import solid from 'vite-plugin-solid'

export default defineConfig({
  plugins: [solid()],
  server: {
    port: 5174,
    proxy: {
      '/api': 'http://localhost:8901',
      '/media': 'http://localhost:8901',
    },
  },
})
