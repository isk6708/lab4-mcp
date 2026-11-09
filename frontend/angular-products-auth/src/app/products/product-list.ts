import { DecimalPipe } from '@angular/common';
import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { errorMessage } from '../shared/error-message';
import { Product } from './product.model';
import { ProductService } from './product.service';

@Component({
  selector: 'app-product-list',
  imports: [RouterLink, DecimalPipe],
  templateUrl: './product-list.html',
  styleUrl: './product-list.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProductList implements OnInit {
  private readonly productService = inject(ProductService);

  // State lives in signals: the view updates automatically when they change
  protected readonly products = signal<Product[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly search = signal('');

  // Derived state: recalculated only when the signals it reads change
  protected readonly filtered = computed(() => {
    const q = this.search().trim().toLowerCase();
    if (!q) {
      return this.products();
    }
    return this.products().filter(
      (p) => p.name.toLowerCase().includes(q) || (p.description ?? '').toLowerCase().includes(q),
    );
  });
  protected readonly stockValue = computed(() =>
    this.products().reduce((sum, p) => sum + p.price * p.stock, 0),
  );

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.productService.list().subscribe({
      next: (products) => {
        this.products.set(products);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  protected onSearch(event: Event): void {
    this.search.set((event.target as HTMLInputElement).value);
  }

  protected remove(product: Product): void {
    if (!confirm(`Delete "${product.name}"? This also deletes its PDF spec sheets.`)) {
      return;
    }
    this.productService.remove(product.id).subscribe({
      next: () => this.products.update((list) => list.filter((p) => p.id !== product.id)),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}
