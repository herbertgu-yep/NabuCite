import { useEffect, useRef } from 'react'
import { init, use } from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { dailyTotals } from './usageData'

use([BarChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer])
export default function UsageTrend({ days, choose }: { days: ReturnType<typeof dailyTotals>; choose: (v: string) => void }) {
  const target = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const chart = init(target.current!, undefined, { renderer: 'svg' })
    chart.setOption({
      animation: false,
      color: ['#395baa', '#a8bce2'],
      grid: { left: 50, right: 16, top: 42, bottom: 32 },
      legend: { right: 8, top: 0 },
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        formatter: (params: { dataIndex: number }[]) => {
          const d = days[params[0].dataIndex]
          return (
            d.day +
            '\n' +
            (d.calls
              ? '输入  ' +
                (d.input ?? '未知') +
                '\n输出  ' +
                (d.output ?? '未知') +
                (d.unknown ? '\n未知用量  ' + d.unknown + ' 次' : '')
              : '暂无调用')
          )
        },
      },
      xAxis: {
        type: 'category',
        data: days.map((d) => d.day),
        axisLabel: { hideOverlap: true, formatter: (s: string) => s.slice(5) },
        axisTick: { show: false },
      },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#edf0f5' } } },
      series: (['input', 'output'] as const).map((key, i) => ({
        name: i ? '输出' : '输入',
        type: 'bar',
        stack: 'tokens',
        barMaxWidth: 34,
        data: days.map((d) => d[key] ?? (d.calls ? null : 0)),
      })),
    })
    chart.on('click', (e) => {
      if (typeof e.name === 'string') choose(e.name)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(target.current!)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [days, choose])
  return <div ref={target} className="trend" role="img" aria-label="每日输入与输出 Token 堆叠柱状图，详细数值见下表" />
}
