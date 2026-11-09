import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthService } from './auth.service';

/**
 * Runs for every HttpClient request:
 *  1. adds "Authorization: Bearer <token>" to calls to our API (never to other sites)
 *  2. on 401 (token missing, wrong or expired) logs out and sends the user to /login
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const isOurApi = req.url.startsWith(environment.apiUrl);
  const isLogin = req.url === `${environment.apiUrl}/token`;
  const token = isOurApi && !isLogin ? auth.token() : null;
  const request = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(request).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && err.status === 401 && isOurApi && !isLogin) {
        const returnUrl = router.url;
        auth.logout(false);
        router.navigate(['/login'], { queryParams: { returnUrl, reason: 'expired' } });
      }
      return throwError(() => err);
    }),
  );
};
