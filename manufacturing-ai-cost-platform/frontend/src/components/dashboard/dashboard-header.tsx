'use client'

import { FlaskConical, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { formatRelativeTime } from '@/lib/format'

const PLANTS = [
  { id: 'all', label: 'All plants' },
  { id: 'plant-pune', label: 'Pune Assembly Plant' },
  { id: 'plant-chennai', label: 'Chennai Powertrain Plant' },
  { id: 'plant-nashik', label: 'Nashik Component Plant' },
]

export function DashboardHeader({
  plantId,
  onPlantChange,
  isDemoData,
  lastUpdated,
  onRefresh,
  isRefreshing,
}: {
  plantId: string
  onPlantChange: (value: string) => void
  isDemoData: boolean
  lastUpdated: string | null
  onRefresh: () => void
  isRefreshing: boolean
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-border pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <h1 className="font-mono text-lg font-semibold tracking-tight text-foreground sm:text-xl">
            AI Cost Intelligence
          </h1>
          {isDemoData && (
            <Badge variant="outline" className="gap-1 border-forecast/40 bg-forecast/10 text-forecast">
              <FlaskConical data-icon="inline-start" />
              Demo data
            </Badge>
          )}
        </div>
        <p className="max-w-xl text-sm text-muted-foreground">
          Enterprise-wide visibility into autonomous AI workload spend, budget health, and optimization opportunities.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={plantId} onValueChange={(value) => onPlantChange(value ?? 'all')}>
          <SelectTrigger aria-label="Filter by plant" className="min-w-44">
            <SelectValue placeholder="All plants" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {PLANTS.map((plant) => (
                <SelectItem key={plant.id} value={plant.id}>
                  {plant.label}
                </SelectItem>
              ))}
            </SelectGroup>
          </SelectContent>
        </Select>

        <Button variant="outline" size="sm" onClick={onRefresh} disabled={isRefreshing}>
          <RotateCw data-icon="inline-start" className={isRefreshing ? 'animate-spin' : undefined} />
          Refresh
        </Button>

        {lastUpdated && (
          <span className="text-xs text-muted-foreground" aria-live="polite">
            Updated {formatRelativeTime(lastUpdated)}
          </span>
        )}
      </div>
    </header>
  )
}
