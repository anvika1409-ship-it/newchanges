'use client'

import { useMemo } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { formatOptionalCurrency, formatOptionalNumber } from '@/lib/format'
import { useWorkloads } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'
import { ProvenanceBadge } from './provenance-badge'

const WORKLOAD_TYPE_LABEL: Record<string, string> = {
  quality_check: 'Quality check',
  predictive_maintenance: 'Predictive maintenance',
  supply_chain: 'Supply chain',
}

export function TopWorkloadsPanel() {
  const workloads = useWorkloads()

  const ranked = useMemo(() => {
    const items = workloads.data?.data ?? []
    // Workloads with no recorded spend sort last rather than being treated
    // as zero-cost, which would rank them alongside genuinely cheap ones.
    return [...items]
      .sort((a, b) => (b.total_cost ?? -1) - (a.total_cost ?? -1))
      .slice(0, 5)
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
              const widthPercent =
                workload.total_cost === null
                  ? 0
                  : Math.max((workload.total_cost / maxCost) * 100, 4)
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
                      {formatOptionalCurrency(workload.total_cost, workload.currency)}
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
                      {(workload.workload_type
                        ? WORKLOAD_TYPE_LABEL[workload.workload_type]
                        : null) ?? 'Unknown type'} &middot;{' '}
                      {workload.plant_label ?? 'Unknown plant'} &middot;{' '}
                      {formatOptionalNumber(workload.request_count)} requests
                    </span>
                    {/* No endpoint returns a per-workload trend. The previous
                        type asserted one, so the panel showed a movement
                        nothing had measured. */}
                    <ProvenanceBadge provenance={workload.provenance} />
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
