import type { MessageKey } from './i18n';
import type { PageSummary } from './types';

export type ActivityTone = 'neutral' | 'active' | 'attention' | 'error';
/** `label`/`detail` are the English text; `labelKey`/`detailKey` select the shown translation. */
export type Activity = {
  label: string; detail: string; tone: ActivityTone;
  labelKey: MessageKey; detailKey: MessageKey; detailParams?: Record<string, string>;
};

function activity(tone: ActivityTone, labelKey: MessageKey, label: string, detailKey: MessageKey, detail: string,
                  detailParams?: Record<string, string>): Activity {
  return { tone, labelKey, label, detailKey, detail, ...(detailParams ? { detailParams } : {}) };
}

/** A readable reason for the Kernel's error code of a failed or paused turn. */
export function failureReason(code: string | null | undefined): { key: MessageKey; text: string; params?: Record<string, string> } | null {
  if (!code) return null;
  if (code.startsWith('web_')) return { key: 'status.failureWeb', text: 'The webpage could not be read. Check that the source is a public HTTPS text page, or try another source.' };
  const http = /^provider_http_(\d{3})$/.exec(code);
  const status = http ? Number(http[1]) : 0;
  if (status === 401 || status === 403) return { key: 'status.failureAuth', text: 'The model service did not accept the API key. Check P_PULSE_API_KEY, then send again.' };
  if (status === 404) return { key: 'status.failureNotFound', text: 'The model service could not find this model or endpoint. Check P_PULSE_CHAT_ENDPOINT and P_PULSE_CHAT_MODEL.' };
  if (status === 429) return { key: 'status.failureRateLimited', text: 'The model service is limiting requests. Wait a moment, then send again.' };
  if (status >= 500) return { key: 'status.failureService', text: 'The model service had an error. Send again in a moment.' };
  if (status >= 400) return { key: 'status.failureRejected', text: 'The model service rejected this request. A very long conversation may need a smaller P_PULSE_CONTEXT_TOKENS.' };
  if (code === 'provider_credential_missing') return { key: 'status.failureNoKey', text: 'No API key is set for the model service. Set P_PULSE_API_KEY and restart Per Pulse.' };
  if (['provider_timeout', 'provider_transport_error', 'provider_closed', 'provider_stream_incomplete'].includes(code)) {
    return { key: 'status.failureUnreachable', text: 'The model service stopped responding. Check the connection, then send again.' };
  }
  if (code === 'payload_too_large') return { key: 'status.failurePayloadTooLarge', text: 'One message or action was too large to save. Review completed work, then continue in smaller steps.' };
  if (code === 'provider_output_limit') return { key: 'status.failureOutputLimit', text: 'The reply reached its length limit in the middle of an action. Ask for a shorter step, or raise P_PULSE_MAX_OUTPUT_TOKENS.' };
  if (code === 'provider_content_filtered') return { key: 'status.failureFiltered', text: "The model service's content filter stopped this reply." };
  if (code === 'provider_stream_too_long') return { key: 'status.failureStreamTooLong', text: 'The reply kept streaming past the time limit (P_PULSE_CHAT_MAX_SECONDS).' };
  if (code === 'provider_generation_incomplete') return { key: 'status.failureIncomplete', text: 'The model service ended the reply early. Send again.' };
  if (code === 'model_loop_limit') return { key: 'status.failureStepLimit', text: 'This turn reached its step limit. Send a message to continue.' };
  if (code === 'world_turn_timeout') return { key: 'status.failureTimeout', text: 'This turn ran out of time.' };
  return { key: 'status.failureOther', text: 'This turn stopped ({code}).', params: { code } };
}

export function getPageActivity(status: PageSummary['status'], configured: boolean, stale = false,
                                failureCode?: string | null, providerWaiting = false): Activity {
  if (providerWaiting) return activity('neutral', 'status.waitingAllowance', 'Waiting for autonomous allowance',
    'status.waitingAllowanceDetail', 'Your work is saved. Open Activity to resume autonomous requests or raise the limit.');
  const reason = status === 'error' || status === 'needs_attention' ? failureReason(failureCode) : null;
  if (status === 'error') {
    return reason
      ? activity('error', 'status.interrupted', 'Turn interrupted', reason.key, reason.text, reason.params)
      : activity('error', 'status.interrupted', 'Turn interrupted', 'status.interruptedDetail', 'Your message is saved. Review the conversation before continuing.');
  }
  if (status === 'needs_attention') {
    return reason
      ? activity('attention', 'status.needsAttention', 'Needs attention', reason.key, reason.text, reason.params)
      : activity('attention', 'status.needsAttention', 'Needs attention', 'status.pausedDetail', 'This turn is paused and needs attention before it can continue.');
  }
  if (stale) return activity('neutral', 'status.updatesPaused', 'Updates paused', 'status.updatesPausedDetail', 'The latest status could not be checked. Refresh to try again.');
  if (status === 'queued' || status === 'working') {
    if (!configured) return activity('neutral', 'status.savedDisconnected', 'Saved · No model configured', 'status.savedDisconnectedDetail', 'Your message is saved. Connect a model to continue this turn.');
    return status === 'working'
      ? activity('active', 'status.working', 'Working', 'status.workingDetail', 'Per Pulse is working in this conversation.')
      : activity('neutral', 'status.queued', 'Queued', 'status.queuedDetail', 'Your message is saved and waiting for its turn.');
  }
  return activity('neutral', 'status.ready', 'Ready', 'status.readyDetail', 'No turn is active. Continue whenever you are ready.');
}

export function getMessageActivity(status: string, configured: boolean, stale = false): Activity | null {
  if (status === 'uncertain') return activity('attention', 'status.needsReview', 'Needs review', 'status.needsReviewDetail', 'The outcome of this turn could not be confirmed. Review the conversation before continuing.');
  if (status === 'failed' || status === 'error') return getPageActivity('error', configured);
  if (status === 'blocked' || status === 'awaiting_approval' || status === 'waiting_approval') {
    return status === 'blocked'
      ? activity('attention', 'status.needsAttention', 'Needs attention', 'status.pausedDetail', 'This turn is paused and needs attention before it can continue.')
      : activity('attention', 'status.needsAttention', 'Needs attention', 'status.approvalDetail', 'Approval is needed before this turn can continue.');
  }
  if (status === 'queued') return getPageActivity('queued', configured, stale);
  if (['running', 'working', 'admitted', 'processing'].includes(status)) return getPageActivity('working', configured, stale);
  if (status === 'cancelled') return activity('neutral', 'status.stopped', 'Stopped', 'status.stoppedDetail', 'This turn stopped. Your saved message is still here.');
  return null;
}
