import { ChangeDetectionStrategy, Component, OnInit, inject, input, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { errorMessage } from '../shared/error-message';
import { AuthService } from './auth.service';

@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Login implements OnInit {
  // Query parameters ?returnUrl=...&reason=... bound by withComponentInputBinding()
  readonly returnUrl = input<string>();
  readonly reason = input<string>();

  private readonly fb = inject(NonNullableFormBuilder);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly form = this.fb.group({
    username: ['', Validators.required],
    password: ['', Validators.required],
  });

  ngOnInit(): void {
    if (this.auth.isLoggedIn()) {
      this.goOn();
    }
  }

  protected submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const { username, password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);
    this.auth.login(username.trim(), password).subscribe({
      next: () => this.goOn(),
      error: (err) => {
        this.error.set(errorMessage(err));
        this.busy.set(false);
      },
    });
  }

  private goOn(): void {
    const target = this.returnUrl();
    // only follow app-internal paths, never an external URL from the query string
    this.router.navigateByUrl(target && target.startsWith('/') && !target.startsWith('//') ? target : '/products');
  }
}
