import { Routes } from '@angular/router';
import { authGuard } from './auth/auth.guard';
import { Login } from './auth/login';
import { ProductForm } from './products/product-form';
import { ProductList } from './products/product-list';

export const routes: Routes = [
  { path: 'login', component: Login, title: 'Sign in' },
  {
    // everything below needs a logged-in user
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'products' },
      { path: 'products', component: ProductList, title: 'Products' },
      { path: 'products/new', component: ProductForm, title: 'New product' },
      { path: 'products/:id/edit', component: ProductForm, title: 'Edit product' },
    ],
  },
  { path: '**', redirectTo: '' },
];
