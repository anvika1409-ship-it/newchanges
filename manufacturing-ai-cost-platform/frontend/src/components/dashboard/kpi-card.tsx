import type { ReactNode } from 'react'
import { TrendingDown, TrendingUp } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { Provenance } from '@/lib/types'
import { ProvenanceBadge } from './provenance-badge'

export function KpiCard({
  label,
  value,
  subtext,
  provenance,
  trendPercent,
  trendIsGood,
  icon,
  tone = 'default',
}: {
  label: string
  value: string
  subtext?: string
  provenance: Provenance
  trendPercent?: number
  trendIsGood?: boolean
  icon: ReactNode
  tone?: 'default' | 'warning' | 'destructive'
}) {
  const showTrend = typeof trendPercent === 'number' && Number.isFinite(trendPercent)
  const TrendIcon = (trendPercent ?? 0) >= 0 ? TrendingUp : TrendingDown

  return (
    <Card
      className={cn(
        'gap-3',
        tone === 'warning' && 'ring-warning/30',
        tone === 'destructive' && 'ring-destructive/30',
      )}
    >
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          {label}
        </CardTitle>
        <span
          className={cn(
            'flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground [&_svg]:size-3.5',
            tone === 'warning' && 'bg-warning/15 text-warning',
            tone === 'destructive' && 'bg-destructive/15 text-destructive',
          )}
          aria-hidden="true"
        >
          {icon}
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <span className="font-mono text-2xl font-semibold tabular-nums text-foreground">{value}</span>
        <div className="flex flex-wrap items-center gap-2">
          <ProvenanceBadge provenance={provenance} />
          {showTrend && (
            <span
              className={cn(
                'inline-flex items-center gap-1 text-xs font-medium tabular-nums',
                trendIsGood === undefined
                  ? 'text-muted-foreground'
                  : trendIsGood
                    ? 'text-primary'
                    : 'text-destructive',
              )}
            >
              <TrendIcon className="size-3.5" aria-hidden="true" />
              {trendPercent!.toFixed(1)}%
            </span>
          )}
        </div>
        {subtext && <p className="text-xs text-muted-foreground">{subtext}</p>}
      </CardContent>
    </Card>
  )
}

export function KpiCardSkeleton() {
  return (
    <Card className="gap-3">
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="size-7 rounded-md" />
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        <Skeleton className="h-7 w-28" />
        <Skeleton className="h-5 w-20 rounded-full" />
      </CardContent>
    </Card>
  )
}
