import { Activity, FlaskConical, Radar, Sigma } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Provenance } from '@/lib/types'

const PROVENANCE_CONFIG: Record<
  Provenance,
  { label: string; icon: typeof Activity; className: string }
> = {
  ACTUAL: {
    label: 'Actual',
    icon: Activity,
    className: 'border-primary/40 bg-primary/10 text-primary',
  },
  ESTIMATED: {
    label: 'Estimated',
    icon: Sigma,
    className: 'border-warning/40 bg-warning/10 text-warning',
  },
  FORECAST: {
    label: 'Forecast',
    icon: Radar,
    className: 'border-forecast/40 bg-forecast/10 text-forecast',
  },
  SIMULATED: {
    label: 'Simulated',
    icon: FlaskConical,
    className: 'border-muted-foreground/30 bg-muted text-muted-foreground',
  },
}

export function ProvenanceBadge({
  provenance,
  className,
}: {
  provenance: Provenance
  className?: string
}) {
  const config = PROVENANCE_CONFIG[provenance]
  const Icon = config.icon
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide',
        config.className,
        className,
      )}
    >
      <Icon className="size-3" aria-hidden="true" />
      {config.label}
    </span>
  )
}

export function ProvenanceLegend() {
  return (
    <div
      className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground"
      role="note"
      aria-label="Data provenance legend"
    >
      <span className="font-medium text-foreground/80">Reading the data:</span>
      {(Object.keys(PROVENANCE_CONFIG) as Provenance[]).map((key) => (
        <span key={key} className="inline-flex items-center gap-1.5">
          <ProvenanceBadge provenance={key} />
        </span>
      ))}
    </div>
  )
}
