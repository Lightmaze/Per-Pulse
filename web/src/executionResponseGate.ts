/**
 * Reject stale native Harness action results after Stop, page changes, or
 * an uncertain action followed by an authoritative recovery read.
 *
 * Only the server's original PageView is authoritative. This gate orders
 * client responses; it never decides whether an action executed.
 */
export class ExecutionResponseGate {
  private pageId: string;
  private revision = 0;

  constructor(pageId: string) {
    this.pageId = pageId;
  }

  forPage(pageId: string): void {
    if (this.pageId !== pageId) {
      this.pageId = pageId;
      this.revision += 1;
    }
  }

  begin(pageId: string): number | null {
    if (pageId !== this.pageId) return null;
    this.revision += 1;
    return this.revision;
  }

  accepts(pageId: string, ticket: number): boolean {
    return this.pageId === pageId && this.revision === ticket;
  }

  invalidate(): void {
    this.revision += 1;
  }
}
