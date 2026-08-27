'use client'

import { useMemo } from 'react'
import { AlertOctagon, CalendarClock, CircleGauge, IndianRupee, PiggyBank, Target, Wallet } from 'lucide-react'
import {
  formatCompactCurrency,
  formatOptionalCurrency,
  formatOptionalPercent,
} from '@/lib/format'
import { useBudgetStatus, useCostSummary, useOptimizationRecommendations } from '@/hooks/use-dashboard-data'
import { KpiCard, KpiCardSkeleton } from './kpi-card'
import { PanelError } from './panel-states'

export function KpiGrid({ plantId }: { plantId?: string }) {
  const summary = useCostSummary({ plantId })
  const budgets = useBudgetStatus()
  const optimizations = useOptimizationRecommendations()

  const isLoading = summary.isLoading || budgets.isLoading || optimizations.isLoading
  const hasError = summary.error || budgets.error || optimizations.error

  const enterpriseBudget = useMemo(
    () => budgets.data?.data.items.find((item) => item.scope_type === 'ENTERPRISE'),
    [budgets.data],
  )

  const totalSavings = useMemo(() => {
    const items = optimizations.data?.data ?? []
    return items
      .filter((item) => item.status === 'APPROVED' || item.status === 'APPLIED')
      // A recommendation with no computed saving contributes nothing, rather
      // than poisoning the total with NaN.
      .reduce((sum, item) => sum + (item.estimated_saving_amount ?? 0), 0)
  }, [optimizations.data])

  if (hasError) {
    return (
      <PanelError
        message="Cost, budget, or savings metrics could not be loaded. Check the connection to the backend and retry."
        onRetry={() => {
          summary.mutate()
          budgets.mutate()
          optimizations.mutate()
        }}
      />
    )
  }

  if (isLoading || !summary.data || !budgets.data) {
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 7 }).map((_, i) => (
          <KpiCardSkeleton key={i} />
        ))}
      </div>
    )
  }

  const cost = summary.data.data
  const currency = cost.currency

  // Overrun is derived here rather than read from the API: /budgets/status
  // returns consumption, not a projected overrun. Deriving it keeps the number
  // traceable to something the API actually sent.
  const consumedAmount = enterpriseBudget?.consumed_amount ?? 0
  const budgetAmount = enterpriseBudget?.amount ?? 0
  const overrunAmount = budgetAmount > 0 ? Math.max(consumedAmount - budgetAmount, 0) : 0
  const overrunPercent = budgetAmount > 0 ? (overrunAmount / budgetAmount) * 100 : 0
  const isOverBudget = overrunAmount > 0

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <KpiCard
        label="Total AI cost"
        value={formatCompactCurrency(cost.total_cost, currency)}
        subtext="Trailing 30-day rolling spend"
        provenance={cost.provenance}
        icon={<IndianRupee />}
      />
      <KpiCard
        label="Today's cost"
        value={formatOptionalCurrency(cost.today_cost, currency)}
        subtext="Needs a windowed /cost/summary call"
        provenance="UNAVAILABLE"
        icon={<CalendarClock />}
      />
      <KpiCard
        label="Monthly cost"
        value={formatOptionalCurrency(cost.month_to_date_cost, currency)}
        subtext="Needs a windowed /cost/summary call"
        provenance="UNAVAILABLE"
        icon={<Wallet />}
      />
      <KpiCard
        label="Budget used"
        value={formatOptionalPercent(cost.budget_consumed_percent)}
        subtext={enterpriseBudget ? enterpriseBudget.scope_label : 'Enterprise monthly budget'}
        provenance={cost.budget_consumed_percent === null ? 'UNAVAILABLE' : 'ACTUAL'}
        tone={
          (cost.budget_consumed_percent ?? 0) >= 90
            ? 'destructive'
            : (cost.budget_consumed_percent ?? 0) >= 80
              ? 'warning'
              : 'default'
        }
        icon={<CircleGauge />}
      />
      <KpiCard
        label="Projected month-end"
        value={formatOptionalCurrency(cost.forecast_month_end_cost, currency)}
        subtext="Forecast at current burn rate"
        provenance="FORECAST"
        icon={<Target />}
      />
      <KpiCard
        label="Projected overrun"
        value={isOverBudget ? formatCompactCurrency(overrunAmount, currency) : 'None'}
        subtext={isOverBudget ? `${overrunPercent.toFixed(1)}% above enterprise budget` : 'Within enterprise budget'}
        provenance="FORECAST"
        tone={isOverBudget ? 'destructive' : 'default'}
        trendPercent={isOverBudget ? overrunPercent : undefined}
        trendIsGood={false}
        icon={<AlertOctagon />}
      />
      <KpiCard
        label="Savings identified"
        value={formatCompactCurrency(totalSavings, currency)}
        subtext="Approved + applied optimization recommendations"
        provenance="SIMULATED"
        icon={<PiggyBank />}
      />
    </div>
  )
}
