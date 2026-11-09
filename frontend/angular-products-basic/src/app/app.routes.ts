import { Routes } from '@angular/router';
import { ProductForm } from './products/product-form';
import { ProductList } from './products/product-list';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'products' },
  { path: 'products', component: ProductList, title: 'Products' },
  { path: 'products/new', component: ProductForm, title: 'New product' },
  { path: 'products/:id/edit', component: ProductForm, title: 'Edit product' },
  { path: '**', redirectTo: 'products' },
];
