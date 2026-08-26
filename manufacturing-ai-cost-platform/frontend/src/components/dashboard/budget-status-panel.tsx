'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress, ProgressTrack, ProgressIndicator } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatCompactCurrency, formatPercent } from '@/lib/format'
import { useBudgetStatus } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'
import type { BudgetStatusItem } from '@/lib/types'

const STATUS_CONFIG: Record<BudgetStatusItem['status'], { label: string; badgeClass: string; barClass: string }> = {
  ON_TRACK: { label: 'On track', badgeClass: 'bg-primary/15 text-primary border-primary/30', barClass: 'bg-primary' },
  WARNING: { label: 'Warning', badgeClass: 'bg-warning/15 text-warning border-warning/30', barClass: 'bg-warning' },
  CRITICAL: {
    label: 'Critical',
    badgeClass: 'bg-destructive/15 text-destructive border-destructive/30',
    barClass: 'bg-destructive',
  },
  EXCEEDED: {
    label: 'Exceeded',
    badgeClass: 'bg-destructive/20 text-destructive border-destructive/40',
    barClass: 'bg-destructive',
  },
}

export function BudgetStatusPanel() {
  const budgets = useBudgetStatus()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Budget consumption</CardTitle>
        <CardDescription>Actual spend against enterprise, plant, and department budgets</CardDescription>
      </CardHeader>
      <CardContent>
        {budgets.error ? (
          <PanelError message="Budget status could not be loaded." onRetry={() => budgets.mutate()} />
        ) : budgets.isLoading || !budgets.data ? (
          <div className="flex flex-col gap-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex flex-col gap-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-2 w-full" />
              </div>
            ))}
          </div>
        ) : budgets.data.data.items.length === 0 ? (
          <PanelEmpty message="No budgets have been configured yet." />
        ) : (
          <ul className="flex flex-col gap-4" role="list">
            {budgets.data.data.items.map((item) => {
              const config = STATUS_CONFIG[item.status]
              return (
                <li key={item.scope_id} className="flex flex-col gap-1.5">
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{item.scope_label}</span>
                      <Badge variant="outline" className={cn('text-[11px]', config.badgeClass)}>
                        {config.label}
                      </Badge>
                    </div>
                    <span className="font-mono text-sm tabular-nums text-muted-foreground">
                      {formatPercent(item.consumed_percent)}
                    </span>
                  </div>
                  <Progress value={Math.min(item.consumed_percent, 100)} aria-label={`${item.scope_label} budget consumed`}>
                    <ProgressTrack>
                      <ProgressIndicator className={config.barClass} />
                    </ProgressTrack>
                  </Progress>
                  <div className="flex items-baseline justify-between text-xs text-muted-foreground">
                    <span>
                      {formatCompactCurrency(item.consumed_amount, item.currency)} of{' '}
                      {formatCompactCurrency(item.amount, item.currency)} ({item.period.toLowerCase()})
                    </span>
                    {item.projected_overrun_amount > 0 && (
                      <span className="text-warning">
                        +{formatCompactCurrency(item.projected_overrun_amount, item.currency)} projected overrun
                      </span>
                    )}
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
