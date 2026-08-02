export interface ProjectionRequestLease {
  requestId: number;
  signal: AbortSignal;
}

export class ProjectionRequestLifecycle {
  private requestId = 0;
  private controller: AbortController | null = null;

  begin(): ProjectionRequestLease {
    this.requestId += 1;
    this.controller?.abort();
    this.controller = new AbortController();
    return { requestId: this.requestId, signal: this.controller.signal };
  }

  invalidate(): void {
    this.requestId += 1;
    this.controller?.abort();
    this.controller = null;
  }

  isCurrent(requestId: number): boolean {
    return this.requestId === requestId;
  }

  finish(requestId: number): void {
    if (this.isCurrent(requestId)) this.controller = null;
  }
}

export interface RunProtocolDimensions {
  process: string;
  wait: string;
  exit: string;
  exitFailed: boolean;
  semantic: string;
}

export function runProtocolDimensions(run: {
  process_status: string | null;
  wait_state: string;
  process_exit_code: number | null;
  timed_out: boolean;
  semantic_result: string | null;
}): RunProtocolDimensions {
  return {
    process: run.process_status ?? "unavailable",
    wait: run.wait_state,
    exit: run.timed_out
      ? "timed out"
      : (run.process_exit_code === null ? "unavailable" : String(run.process_exit_code)),
    exitFailed: run.timed_out || (
      run.process_exit_code !== null && run.process_exit_code !== 0
    ),
    semantic: run.semantic_result ?? "unavailable",
  };
}
