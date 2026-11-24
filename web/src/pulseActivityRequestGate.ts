/**
 * Fence Activity reads against operator writes and out-of-order polling.
 *
 * This is a client-only request ordering guard, never a second Pulse state
 * authority. GET/PATCH responses still come from the native controller.
 */
export type PulseReadTicket = Readonly<{ version: number; sequence: number }>;

export class PulseActivityRequestGate {
  private version = 0;
  private sequence = 0;
  private writing = false;

  beginRead(): PulseReadTicket | null {
    if (this.writing) return null;
    return { version: this.version, sequence: ++this.sequence };
  }

  acceptsRead(ticket: PulseReadTicket): boolean {
    return !this.writing && ticket.version === this.version && ticket.sequence === this.sequence;
  }

  beginWrite(): boolean {
    if (this.writing) return false;
    this.writing = true;
    this.version += 1; // Invalidate GETs started before PATCH.
    return true;
  }

  finishWrite(): void {
    if (!this.writing) return;
    this.writing = false;
    this.version += 1; // Invalidate anything observed during PATCH.
  }
}
