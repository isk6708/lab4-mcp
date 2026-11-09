import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { Product, ProductInput } from './product.model';

/** All HTTP calls for products live here, so components never build URLs themselves. */
@Injectable({ providedIn: 'root' })
export class ProductService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/products`; // open API: no login needed

  list(): Observable<Product[]> {
    return this.http.get<Product[]>(this.url);
  }

  get(id: number): Observable<Product> {
    return this.http.get<Product>(`${this.url}/${id}`);
  }

  create(product: ProductInput): Observable<Product> {
    return this.http.post<Product>(this.url, product);
  }

  update(id: number, product: ProductInput): Observable<Product> {
    return this.http.put<Product>(`${this.url}/${id}`, product);
  }

  remove(id: number): Observable<void> {
    return this.http.delete<void>(`${this.url}/${id}`);
  }
}
