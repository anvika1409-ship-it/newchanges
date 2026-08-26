'use client'

import { AlertOctagon, AlertTriangle, CircleAlert, Info } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatCompactCurrency, formatRelativeTime } from '@/lib/format'
import { useAnomalies } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'
import type { Anomaly } from '@/lib/types'

const SEVERITY_CONFIG: Record<Anomaly['severity'], { icon: typeof Info; className: string }> = {
  LOW: { icon: Info, className: 'bg-muted text-muted-foreground border-border' },
  MEDIUM: { icon: CircleAlert, className: 'bg-warning/15 text-warning border-warning/30' },
  HIGH: { icon: AlertTriangle, className: 'bg-warning/25 text-warning border-warning/40' },
  CRITICAL: { icon: AlertOctagon, className: 'bg-destructive/15 text-destructive border-destructive/30' },
}

export function AnomaliesPanel() {
  const anomalies = useAnomalies()
  const items = anomalies.data?.data ?? []
  const openCount = items.filter((a) => a.status === 'OPEN').length

  return (
    <Card>
      <CardHeader>
        <CardTitle>Anomalies</CardTitle>
        <CardDescription>
          {openCount > 0
            ? `${openCount} open anomal${openCount === 1 ? 'y' : 'ies'} require review`
            : 'Detected deviations in AI spend patterns'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {anomalies.error ? (
          <PanelError message="Anomalies could not be loaded." onRetry={() => anomalies.mutate()} />
        ) : anomalies.isLoading || !anomalies.data ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <PanelEmpty title="No anomalies detected" message="AI spend has stayed within expected patterns." />
        ) : (
          <ul className="flex flex-col gap-3" role="list">
            {items.map((anomaly) => {
              const config = SEVERITY_CONFIG[anomaly.severity]
              const Icon = config.icon
              return (
                <li
                  key={anomaly.id}
                  className={cn('flex gap-3 rounded-lg border p-3', config.className, 'bg-opacity-100')}
                >
                  <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-medium text-foreground">{anomaly.scope_label}</span>
                      <div className="flex items-center gap-1.5">
                        <Badge variant="outline" className="text-[11px]">
                          {anomaly.severity}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatRelativeTime(anomaly.detected_at)}</span>
                      </div>
                    </div>
                    <p className="text-sm text-muted-foreground">{anomaly.summary}</p>
                    <p className="font-mono text-xs tabular-nums text-muted-foreground">
                      Expected {formatCompactCurrency(anomaly.expected_value, anomaly.currency)} &rarr; observed{' '}
                      {formatCompactCurrency(anomaly.observed_value, anomaly.currency)} (
                      {anomaly.deviation_percent > 0 ? '+' : ''}
                      {anomaly.deviation_percent.toFixed(0)}%)
                    </p>
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
