'use client'

import { useMemo } from 'react'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { formatCompactCurrency, formatNumber } from '@/lib/format'
import { useWorkloads } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'

const WORKLOAD_TYPE_LABEL: Record<string, string> = {
  quality_check: 'Quality check',
  predictive_maintenance: 'Predictive maintenance',
  supply_chain: 'Supply chain',
}

export function TopWorkloadsPanel() {
  const workloads = useWorkloads()

  const ranked = useMemo(() => {
    const items = workloads.data?.data ?? []
    return [...items].sort((a, b) => b.total_cost - a.total_cost).slice(0, 5)
  }, [workloads.data])

  const maxCost = ranked[0]?.total_cost ?? 1

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top expensive workloads</CardTitle>
        <CardDescription>Highest AI spend by workload over the current period</CardDescription>
      </CardHeader>
      <CardContent>
        {workloads.error ? (
          <PanelError message="Workload spend could not be loaded." onRetry={() => workloads.mutate()} />
        ) : workloads.isLoading || !workloads.data ? (
          <div className="flex flex-col gap-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : ranked.length === 0 ? (
          <PanelEmpty message="No AI workloads have recorded spend yet." />
        ) : (
          <ul className="flex flex-col gap-4" role="list">
            {ranked.map((workload, index) => {
              const widthPercent = Math.max((workload.total_cost / maxCost) * 100, 4)
              const TrendIcon = workload.trend_percent >= 0 ? TrendingUp : TrendingDown
              const trendIsRegression = workload.trend_percent >= 0
              return (
                <li key={workload.id} className="flex flex-col gap-1.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="flex min-w-0 items-baseline gap-2">
                      <span className="font-mono text-xs text-muted-foreground">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="truncate text-sm font-medium text-foreground">{workload.label}</span>
                    </div>
                    <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-foreground">
                      {formatCompactCurrency(workload.total_cost, workload.currency)}
                    </span>
                  </div>
                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted" aria-hidden="true">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${widthPercent}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {WORKLOAD_TYPE_LABEL[workload.workload_type] ?? workload.workload_type} &middot;{' '}
                      {workload.plant_label} &middot; {formatNumber(workload.request_count)} requests
                    </span>
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 tabular-nums',
                        trendIsRegression ? 'text-destructive' : 'text-primary',
                      )}
                    >
                      <TrendIcon className="size-3" aria-hidden="true" />
                      {Math.abs(workload.trend_percent).toFixed(1)}%
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
