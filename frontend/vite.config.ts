import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const local = loadEnv(mode, '.', 'NABUCITE_API_TARGET')
  const target = process.env.NABUCITE_API_TARGET || local.NABUCITE_API_TARGET || process.env.VITE_API_BASE_URL ||
    loadEnv(mode, '.', 'VITE_API_BASE_URL').VITE_API_BASE_URL || 'http://127.0.0.1:8000'
  return {
    server: { proxy: { '/api': { target, changeOrigin: true } } },
    // 首次打开用量页时重新预构建会刷新正在接收回答的其他标签页。
    optimizeDeps: { include: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'] },
  }
})
