'use client'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatOptionalCurrency } from '@/lib/format'
import { useOptimizationRecommendations } from '@/hooks/use-dashboard-data'
import { PanelEmpty, PanelError } from './panel-states'
import { ProvenanceBadge } from './provenance-badge'
import type { OptimizationRecommendationView } from '@/lib/types'

// Lifecycle states from DATABASE_SCHEMA.md section 18. The previous map used
// PROPOSED, which no endpoint returns.
const STATUS_LABEL: Record<OptimizationRecommendationView['status'], string> = {
  DRAFT: 'Draft',
  PENDING_APPROVAL: 'Pending approval',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  APPLIED: 'Applied',
  ROLLED_BACK: 'Rolled back',
}

const RISK_CLASS: Record<OptimizationRecommendationView['risk_level'], string> = {
  LOW: 'border-primary/30 bg-primary/10 text-primary',
  MEDIUM: 'border-warning/30 bg-warning/10 text-warning',
  HIGH: 'border-destructive/30 bg-destructive/10 text-destructive',
  CRITICAL: 'border-destructive/40 bg-destructive/15 text-destructive',
}

export function OptimizationPanel() {
  const optimizations = useOptimizationRecommendations()
  const items = optimizations.data?.data ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle>Optimization opportunities</CardTitle>
        <CardDescription>Simulated routing and caching changes, pending human approval</CardDescription>
      </CardHeader>
      <CardContent>
        {optimizations.error ? (
          <PanelError message="Optimization recommendations could not be loaded." onRetry={() => optimizations.mutate()} />
        ) : optimizations.isLoading || !optimizations.data ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <PanelEmpty title="No recommendations yet" message="The optimization engine has not surfaced any changes." />
        ) : (
          <ul className="flex flex-col gap-3" role="list">
            {items.map((rec) => (
              <li key={rec.id} className="flex flex-col gap-1.5 rounded-lg border border-border p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="flex min-w-0 flex-col">
                    <span className="text-sm font-medium text-foreground">{rec.title}</span>
                    <span className="text-xs text-muted-foreground">{rec.workload_label}</span>
                  </div>
                  <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-primary">
                    {formatOptionalCurrency(rec.estimated_saving_amount, rec.currency)}
                  </span>
                </div>
                <p className="text-sm text-muted-foreground">{rec.description}</p>
                <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                  <ProvenanceBadge provenance={rec.provenance} />
                  <Badge variant="outline" className={cn('text-[11px]', RISK_CLASS[rec.risk_level])}>
                    {rec.risk_level} risk
                  </Badge>
                  <Badge variant="secondary" className="text-[11px]">
                    {STATUS_LABEL[rec.status]}
                  </Badge>
                  {rec.simulation_only && (
                    <span className="text-[11px] text-muted-foreground">Requires approval before activation</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
