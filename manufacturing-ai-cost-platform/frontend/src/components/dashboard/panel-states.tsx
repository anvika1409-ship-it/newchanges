'use client'

import { AlertTriangle, Inbox, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'

export function PanelError({
  title = 'Data unavailable',
  message,
  onRetry,
}: {
  title?: string
  message: string
  onRetry?: () => void
}) {
  return (
    <Empty className="border border-destructive/30 bg-destructive/5 py-10">
      <EmptyHeader>
        <EmptyMedia variant="icon" className="bg-destructive/15 text-destructive">
          <AlertTriangle aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{message}</EmptyDescription>
      </EmptyHeader>
      {onRetry && (
        <EmptyContent>
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RotateCw data-icon="inline-start" />
            Retry
          </Button>
        </EmptyContent>
      )}
    </Empty>
  )
}

export function PanelEmpty({
  title = 'Nothing to show',
  message,
}: {
  title?: string
  message: string
}) {
  return (
    <Empty className="border border-border py-10">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <Inbox aria-hidden="true" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{message}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
