import { HttpErrorResponse } from '@angular/common/http';
import { environment } from '../../environments/environment';

/** Turn an HTTP error into a sentence a user can act on. FastAPI puts details in `error.detail`. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 0) {
      return `Cannot reach the API at ${environment.apiUrl}. Is it running, and does its CORS_ORIGINS include this page's address?`;
    }
    const detail: unknown = err.error?.detail;
    if (typeof detail === 'string') {
      return `${err.status}: ${detail}`;
    }
    if (Array.isArray(detail)) {
      // 422 validation errors: [{ loc: [...], msg: '...' }, ...]
      return `${err.status}: ${detail.map((d: { msg?: string }) => d.msg ?? 'invalid value').join('; ')}`;
    }
    return `${err.status}: ${err.statusText || 'Request failed'}`;
  }
  return 'Unexpected error';
}
