import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, map } from 'rxjs';
import { environment } from '../../environments/environment';

/** Response of POST /token (FastAPI OAuth2 password flow). */
interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number; // seconds
  username: string;
}

/** What we keep after login. */
interface Session {
  token: string;
  username: string;
  expiresAt: number; // epoch milliseconds
}

const STORAGE_KEY = 'lab4.session';

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const session = raw ? (JSON.parse(raw) as Session) : null;
    return session && session.expiresAt > Date.now() ? session : null;
  } catch {
    return null; // storage blocked or corrupt: just start logged out
  }
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  // Survives page reloads through localStorage. (Teaching note: localStorage is readable by any
  // script on the page, so XSS can steal it; production apps often prefer httpOnly cookies.)
  private readonly session = signal<Session | null>(readStoredSession());

  readonly username = computed(() => this.session()?.username ?? null);
  readonly expiresAt = computed(() => this.session()?.expiresAt ?? null);

  /** The token, or null if not logged in or expired. */
  token(): string | null {
    const session = this.session();
    if (!session) {
      return null;
    }
    if (Date.now() >= session.expiresAt) {
      this.clear();
      return null;
    }
    return session.token;
  }

  isLoggedIn(): boolean {
    return this.token() !== null;
  }

  /** POST /token with form fields username + password (OAuth2 expects a form, not JSON). */
  login(username: string, password: string): Observable<void> {
    const body = new HttpParams().set('username', username).set('password', password);
    return this.http.post<TokenResponse>(`${environment.apiUrl}/token`, body).pipe(
      map((res) => {
        const session: Session = {
          token: res.access_token,
          username: res.username,
          expiresAt: Date.now() + res.expires_in * 1000,
        };
        this.session.set(session);
        try {
          localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
        } catch {
          // storage unavailable: the session still works until the page is reloaded
        }
      }),
    );
  }

  logout(redirect = true): void {
    this.clear();
    if (redirect) {
      this.router.navigate(['/login']);
    }
  }

  private clear(): void {
    this.session.set(null);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }
}
