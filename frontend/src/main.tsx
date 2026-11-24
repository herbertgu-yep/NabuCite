import { createRoot } from 'react-dom/client'
import { App as AntApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { HashRouter } from 'react-router-dom'
import { App } from './App'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: '#395baa',
        colorText: '#344058',
        colorBorder: '#e6e8f1',
        borderRadius: 8,
        fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',
      },
    }}
  >
    <AntApp>
      <HashRouter useTransitions={false}>
        <App />
      </HashRouter>
    </AntApp>
  </ConfigProvider>,
)
