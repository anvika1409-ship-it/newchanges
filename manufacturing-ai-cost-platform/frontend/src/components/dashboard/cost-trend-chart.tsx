'use client'

import { useMemo } from 'react'
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from '@/components/ui/chart'
import { formatCompactCurrency, formatShortDate } from '@/lib/format'
import { useCostTrend, useForecast } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'
import { ProvenanceBadge } from './provenance-badge'

const chartConfig: ChartConfig = {
  actual: { label: 'Actual spend', color: 'var(--chart-1)' },
  forecast: { label: 'Forecasted spend', color: 'var(--chart-3)' },
}

export function CostTrendChart() {
  const trend = useCostTrend()
  const forecast = useForecast()

  const merged = useMemo(() => {
    const actualPoints = trend.data?.data.points ?? []
    const forecastPoints = forecast.data?.data.points ?? []

    const rows = new Map<string, { timestamp: string; actual?: number; forecast?: number }>()

    for (const point of actualPoints) {
      if (point.provenance === 'ACTUAL') {
        rows.set(point.timestamp, { timestamp: point.timestamp, actual: point.cost })
      }
    }

    // Bridge the last actual point into the forecast so the lines connect visually.
    const lastActual = [...rows.values()].at(-1)
    if (lastActual) {
      rows.set(`bridge-${lastActual.timestamp}`, { ...lastActual, forecast: lastActual.actual })
    }

    for (const point of forecastPoints.slice(0, 10)) {
      rows.set(point.timestamp, { timestamp: point.timestamp, forecast: point.forecast_cost })
    }

    return [...rows.values()].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
  }, [trend.data, forecast.data])

  const currency = trend.data?.data.currency ?? forecast.data?.data.currency ?? 'INR'
  const isLoading = trend.isLoading || forecast.isLoading
  const hasError = trend.error || forecast.error

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-4 space-y-0">
        <div>
          <CardTitle>AI spend trend</CardTitle>
          <CardDescription>Daily spend over the last 30 days, with a 7-day forward forecast</CardDescription>
        </div>
        <div className="flex gap-1.5">
          <ProvenanceBadge provenance="ACTUAL" />
          <ProvenanceBadge provenance="FORECAST" />
        </div>
      </CardHeader>
      <CardContent>
        {hasError ? (
          <PanelError
            message="The cost trend could not be loaded."
            onRetry={() => {
              trend.mutate()
              forecast.mutate()
            }}
          />
        ) : isLoading ? (
          <Skeleton className="aspect-video w-full" />
        ) : merged.length === 0 ? (
          <PanelEmpty message="No cost trend data is available for the selected period yet." />
        ) : (
          <ChartContainer config={chartConfig} className="aspect-auto h-[280px] w-full">
            <AreaChart data={merged} margin={{ left: 4, right: 4, top: 8 }}>
              <defs>
                <linearGradient id="fillActual" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--color-actual)" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="var(--color-actual)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="timestamp"
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                minTickGap={32}
                tickFormatter={(value: string) => formatShortDate(value)}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                tickMargin={8}
                width={56}
                tickFormatter={(value: number) => formatCompactCurrency(value, currency)}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelFormatter={(value) => formatShortDate(String(value))}
                    formatter={(value, name) => [
                      formatCompactCurrency(Number(value), currency),
                      name === 'actual' ? 'Actual' : 'Forecast',
                    ]}
                  />
                }
              />
              <Area
                dataKey="actual"
                type="monotone"
                stroke="var(--color-actual)"
                fill="url(#fillActual)"
                strokeWidth={2}
                connectNulls
              />
              <Area
                dataKey="forecast"
                type="monotone"
                stroke="var(--color-forecast)"
                fill="none"
                strokeWidth={2}
                strokeDasharray="6 4"
                connectNulls
              />
            </AreaChart>
          </ChartContainer>
        )}
      </CardContent>
    </Card>
  )
}
