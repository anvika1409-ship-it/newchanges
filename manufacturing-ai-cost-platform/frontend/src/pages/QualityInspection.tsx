import React, { useState, useRef } from 'react';
import {
  Upload,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
  Coins,
  Cpu,
  Layers,
  Sparkles,
  RefreshCw,
  Eye,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { ProvenanceBadge } from '@/components/dashboard/provenance-badge';
import { uploadImage, executeQualityCheck } from '@/services/quality';
import type { AIExecutionResponse, InputRef } from '@/types/quality';
import type { Provenance } from '@/lib/types';
import { formatCurrency, formatPercent } from '@/lib/format';

export function QualityInspection() {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isInspecting, setIsInspecting] = useState(false);
  const [uploadedRef, setUploadedRef] = useState<InputRef | null>(null);
  const [inspectionResult, setInspectionResult] = useState<AIExecutionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [priority, setPriority] = useState<'LOW' | 'NORMAL' | 'HIGH' | 'CRITICAL'>('NORMAL');

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith('image/')) {
      setError('Please select a valid image file (JPEG, PNG, WEBP).');
      return;
    }

    if (file.size > 5 * 1024 * 1024) {
      setError('Image file size must be less than 5MB.');
      return;
    }

    setSelectedFile(file);
    setPreviewUrl(URL.createObjectURL(file));
    setError(null);
    setUploadedRef(null);
    setInspectionResult(null);
  };

  const handleUploadAndInspect = async () => {
    if (!selectedFile) return;

    setError(null);
    setIsUploading(true);

    try {
      // Step 1: Upload image to secure backend storage
      const inputRef = await uploadImage(selectedFile);
      setUploadedRef(inputRef);
      setIsUploading(false);

      // Step 2: Execute quality check through Cost-Aware Orchestrator
      setIsInspecting(true);
      const response = await executeQualityCheck(inputRef, {
        businessPriority: priority,
      });
      setInspectionResult(response);
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : 'An error occurred during quality inspection.';
      setError(message);
    } finally {
      setIsUploading(false);
      setIsInspecting(false);
    }
  };

  const handleReset = () => {
    setSelectedFile(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setPreviewUrl(null);
    setUploadedRef(null);
    setInspectionResult(null);
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const verdict = inspectionResult?.result?.verdict;
  const isPass = verdict === 'PASS';
  const isFail = verdict === 'FAIL';
  const isInconclusive = verdict === 'INCONCLUSIVE';

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <Eye className="size-6 text-primary" />
            Manufacturing Quality Inspection
          </h1>
          <p className="text-sm text-muted-foreground">
            Vision-guided product defect detection with cost-aware routing and model selection
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="border-primary/30 text-primary">
            Workload: quality_check
          </Badge>
          <Badge variant="outline">Modalities: Image, Text</Badge>
        </div>
      </div>

      {/* Error Alert */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle className="size-4" />
          <AlertTitle>Inspection Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Main Grid: Left Upload & Parameters, Right Result */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Left Column: Image Upload & Parameters */}
        <div className="space-y-6 lg:col-span-5">
          <Card>
            <CardHeader>
              <CardTitle className="text-base font-semibold">Product Image Submission</CardTitle>
              <CardDescription>
                Upload an image of the manufactured part to verify quality and detect defects.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={handleFileChange}
              />

              {!previewUrl ? (
                <div
                  onClick={() => fileInputRef.current?.click()}
                  className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 p-8 text-center transition-colors hover:border-primary/50 hover:bg-muted/30 cursor-pointer"
                >
                  <div className="rounded-full bg-primary/10 p-3 text-primary mb-3">
                    <Upload className="size-6" />
                  </div>
                  <p className="text-sm font-medium text-foreground">
                    Click to select or drag and drop image
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">PNG, JPG, WEBP up to 5MB</p>
                </div>
              ) : (
                <div className="space-y-3">
                  <div className="relative overflow-hidden rounded-lg border border-border bg-muted/20">
                    <img
                      src={previewUrl}
                      alt="Product inspection preview"
                      className="max-h-64 w-full object-contain"
                    />
                  </div>
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>{selectedFile?.name}</span>
                    <span>{selectedFile ? (selectedFile.size / 1024).toFixed(1) + ' KB' : ''}</span>
                  </div>
                </div>
              )}

              {/* Priority Selector */}
              <div className="space-y-2 pt-2">
                <label className="text-xs font-medium text-muted-foreground">Business Priority</label>
                <div className="grid grid-cols-4 gap-2">
                  {(['LOW', 'NORMAL', 'HIGH', 'CRITICAL'] as const).map((p) => (
                    <Button
                      key={p}
                      type="button"
                      variant={priority === p ? 'default' : 'outline'}
                      size="sm"
                      className="text-xs h-8"
                      onClick={() => setPriority(p)}
                      disabled={isUploading || isInspecting}
                    >
                      {p}
                    </Button>
                  ))}
                </div>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-3 pt-2">
                <Button
                  className="flex-1 gap-2"
                  onClick={handleUploadAndInspect}
                  disabled={!selectedFile || isUploading || isInspecting}
                >
                  {isUploading ? (
                    <>
                      <RefreshCw className="size-4 animate-spin" />
                      Uploading Image...
                    </>
                  ) : isInspecting ? (
                    <>
                      <Sparkles className="size-4 animate-pulse text-amber-300" />
                      Routing & Inspecting...
                    </>
                  ) : (
                    <>
                      <Sparkles className="size-4" />
                      Run Quality Check
                    </>
                  )}
                </Button>
                {selectedFile && (
                  <Button
                    variant="outline"
                    onClick={handleReset}
                    disabled={isUploading || isInspecting}
                  >
                    Reset
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Architecture Information Card */}
          <Card className="bg-muted/20 border-muted">
            <CardHeader className="py-3 px-4">
              <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Inspection Workflow
              </CardTitle>
            </CardHeader>
            <CardContent className="px-4 pb-4 text-xs text-muted-foreground space-y-1.5">
              <p>1. Image is securely stored by reference (no URL passed to model).</p>
              <p>2. Cost-Aware Orchestrator classifies complexity deterministically.</p>
              <p>3. Active routing policy selects vision model (Phi-3.5 or Llama-3.2-90B).</p>
              <p>4. Telemetry and cost provenance persisted to SQLite.</p>
            </CardContent>
          </Card>
        </div>

        {/* Right Column: Inspection Results & Telemetry */}
        <div className="space-y-6 lg:col-span-7">
          {isInspecting ? (
            <Card className="border-primary/40 bg-primary/5">
              <CardHeader>
                <div className="flex items-center gap-2">
                  <RefreshCw className="size-5 animate-spin text-primary" />
                  <CardTitle className="text-base">Executing Quality Inspection</CardTitle>
                </div>
                <CardDescription>
                  Evaluating complexity, verifying budget limits, selecting vision model, and performing defect analysis...
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <Skeleton className="h-20 w-full" />
                <div className="grid grid-cols-2 gap-4">
                  <Skeleton className="h-16 w-full" />
                  <Skeleton className="h-16 w-full" />
                </div>
              </CardContent>
            </Card>
          ) : inspectionResult ? (
            <div className="space-y-6">
              {/* Verdict Banner Card */}
              <Card
                className={`border-2 ${
                  isPass
                    ? 'border-emerald-500/40 bg-emerald-500/10'
                    : isFail
                    ? 'border-rose-500/40 bg-rose-500/10'
                    : 'border-amber-500/40 bg-amber-500/10'
                }`}
              >
                <CardContent className="flex items-center justify-between p-6">
                  <div className="flex items-center gap-4">
                    <div
                      className={`rounded-full p-3 ${
                        isPass
                          ? 'bg-emerald-500/20 text-emerald-400'
                          : isFail
                          ? 'bg-rose-500/20 text-rose-400'
                          : 'bg-amber-500/20 text-amber-400'
                      }`}
                    >
                      {isPass ? (
                        <CheckCircle2 className="size-8" />
                      ) : isFail ? (
                        <XCircle className="size-8" />
                      ) : (
                        <AlertCircle className="size-8" />
                      )}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-xl font-bold tracking-tight text-foreground">
                          VERDICT: {verdict}
                        </span>
                        <Badge
                          variant={isPass ? 'default' : isFail ? 'destructive' : 'secondary'}
                          className="text-xs"
                        >
                          {inspectionResult.result.defect_type
                            ? `Defect: ${inspectionResult.result.defect_type}`
                            : isPass
                            ? 'No Defects Detected'
                            : 'Inconclusive'}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        Request ID: <span className="font-mono">{inspectionResult.request_id}</span>
                      </p>
                    </div>
                  </div>

                  {inspectionResult.result.confidence !== null && (
                    <div className="text-right">
                      <div className="text-xs text-muted-foreground">Confidence</div>
                      <div className="text-2xl font-bold text-foreground">
                        {formatPercent(inspectionResult.result.confidence * 100, 1)}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Execution Metrics Grid */}
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {/* Selected Model */}
                <Card className="bg-card">
                  <CardHeader className="p-4 pb-1">
                    <CardTitle className="text-xs text-muted-foreground flex items-center gap-1.5 font-normal">
                      <Cpu className="size-3.5" /> Selected Model
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 pt-1">
                    <p className="text-sm font-semibold truncate text-foreground" title={inspectionResult.execution_plan.selected_model_id ?? ''}>
                      {inspectionResult.execution_plan.selected_model_id
                        ? inspectionResult.execution_plan.selected_model_id.replace('azure_ai/genailab-maas-', '')
                        : 'Default Vision'}
                    </p>
                    <span className="text-[10px] text-muted-foreground">Decided pre-execution</span>
                  </CardContent>
                </Card>

                {/* Cost with Provenance */}
                <Card className="bg-card">
                  <CardHeader className="p-4 pb-1">
                    <CardTitle className="text-xs text-muted-foreground flex items-center gap-1.5 font-normal">
                      <Coins className="size-3.5" /> Cost
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 pt-1">
                    <div className="text-sm font-semibold text-foreground">
                      {inspectionResult.cost.amount !== null
                        ? formatCurrency(inspectionResult.cost.amount, inspectionResult.cost.currency || 'USD')
                        : 'Unavailable'}
                    </div>
                    <div className="mt-1">
                      <ProvenanceBadge
                        provenance={inspectionResult.cost.provenance as Provenance}
                      />
                    </div>
                  </CardContent>
                </Card>

                {/* Complexity Tier */}
                <Card className="bg-card">
                  <CardHeader className="p-4 pb-1">
                    <CardTitle className="text-xs text-muted-foreground flex items-center gap-1.5 font-normal">
                      <Layers className="size-3.5" /> Complexity
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 pt-1">
                    <div className="text-sm font-semibold text-foreground">
                      {inspectionResult.execution_plan.complexity}
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      Risk: {inspectionResult.execution_plan.risk_level}
                    </span>
                  </CardContent>
                </Card>

                {/* Budget Decision */}
                <Card className="bg-card">
                  <CardHeader className="p-4 pb-1">
                    <CardTitle className="text-xs text-muted-foreground flex items-center gap-1.5 font-normal">
                      <Clock className="size-3.5" /> Budget Status
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-4 pt-1">
                    <div className="text-sm font-semibold text-foreground">
                      {inspectionResult.execution_plan.budget_status}
                    </div>
                    <span className="text-[10px] text-muted-foreground">
                      Tokens: {inspectionResult.usage.total_tokens ?? 'N/A'}
                    </span>
                  </CardContent>
                </Card>
              </div>

              {/* Raw Response Details */}
              <Card>
                <CardHeader className="p-4 pb-2">
                  <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    Model Analysis Output
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-4 pt-0">
                  <div className="rounded-md bg-muted/40 p-3 font-mono text-xs text-foreground whitespace-pre-wrap max-h-48 overflow-y-auto">
                    {inspectionResult.result.raw_response || inspectionResult.result.content}
                  </div>
                </CardContent>
              </Card>
            </div>
          ) : (
            <Card className="flex flex-col items-center justify-center p-12 text-center text-muted-foreground border-dashed">
              <Eye className="size-12 text-muted-foreground/40 mb-3" />
              <CardTitle className="text-base text-foreground">No Inspection Run Yet</CardTitle>
              <CardDescription className="max-w-sm mt-1">
                Upload a product image on the left and click "Run Quality Check" to inspect for manufacturing defects.
              </CardDescription>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
