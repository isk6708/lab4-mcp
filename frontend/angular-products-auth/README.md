# Lab 4 Angular v2: login first, then products

Same product CRUD as v1, but against `/secure/products`, which requires a JWT:

- `auth/auth.service.ts`: `POST /token`, keeps the session in a signal and localStorage
- `auth/auth.interceptor.ts`: adds `Authorization: Bearer <token>`, handles 401
- `auth/auth.guard.ts`: sends signed-out users to `/login?returnUrl=...`

```cmd
npm install
npm start
```

Open http://localhost:4201 and sign in with the lab user `admin` / `123456`.

Needs Node.js 22.22.3+ or 24.15+ (Angular 22). Full guide: section 11 of the main `README.md`.
