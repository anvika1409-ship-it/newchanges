'use client'

import { useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { DashboardHeader } from './dashboard-header'
import { KpiGrid } from './kpi-grid'
import { CostTrendChart } from './cost-trend-chart'
import { TopWorkloadsPanel } from './top-workloads'
import { AnomaliesPanel } from './anomalies-panel'
import { OptimizationPanel } from './optimization-panel'
import { BudgetStatusPanel } from './budget-status-panel'
import { ProvenanceLegend } from './provenance-badge'
import {
  useAnomalies,
  useBudgetStatus,
  useCostSummary,
  useCostTrend,
  useForecast,
  useOptimizationRecommendations,
  useWorkloads,
} from '@/hooks/use-dashboard-data'

const sectionVariants = {
  hidden: { opacity: 0, y: 12 },
  visible: { opacity: 1, y: 0 },
}

const container = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.06 } },
}

export function DashboardShell() {
  const [plantId, setPlantId] = useState('all')

  const summary = useCostSummary({ plantId: plantId === 'all' ? undefined : plantId })
  const trend = useCostTrend()
  const budgets = useBudgetStatus()
  const forecast = useForecast()
  const anomalies = useAnomalies()
  const optimizations = useOptimizationRecommendations()
  const workloads = useWorkloads()

  const isDemoData = [summary, trend, budgets, forecast, anomalies, optimizations, workloads].some(
    (hook) => hook.data?.source === 'demo',
  )

  const isRefreshing = [summary, trend, budgets, forecast, anomalies, optimizations, workloads].some(
    (hook) => hook.isValidating,
  )

  const lastUpdated = summary.data?.fetched_at ?? null

  const handleRefresh = useCallback(() => {
    summary.mutate()
    trend.mutate()
    budgets.mutate()
    forecast.mutate()
    anomalies.mutate()
    optimizations.mutate()
    workloads.mutate()
  }, [summary, trend, budgets, forecast, anomalies, optimizations, workloads])

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-8 px-4 py-8 sm:px-6 lg:px-8">
      <DashboardHeader
        plantId={plantId}
        onPlantChange={setPlantId}
        isDemoData={isDemoData}
        lastUpdated={lastUpdated}
        onRefresh={handleRefresh}
        isRefreshing={isRefreshing}
      />

      <motion.div
        initial="hidden"
        animate="visible"
        variants={container}
        className="flex flex-col gap-8"
      >
        <motion.section variants={sectionVariants} aria-label="Key cost metrics">
          <KpiGrid plantId={plantId === 'all' ? undefined : plantId} />
        </motion.section>

        <motion.section variants={sectionVariants} aria-label="Spend trend">
          <CostTrendChart />
        </motion.section>

        <motion.div variants={sectionVariants} className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <section aria-label="Top expensive workloads">
            <TopWorkloadsPanel />
          </section>
          <section aria-label="Budget status">
            <BudgetStatusPanel />
          </section>
        </motion.div>

        <motion.div variants={sectionVariants} className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <section aria-label="Anomalies">
            <AnomaliesPanel />
          </section>
          <section aria-label="Optimization opportunities">
            <OptimizationPanel />
          </section>
        </motion.div>

        <motion.footer variants={sectionVariants} className="border-t border-border pt-6">
          <ProvenanceLegend />
        </motion.footer>
      </motion.div>
    </div>
  )
}
