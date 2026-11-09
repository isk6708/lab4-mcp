import { ChangeDetectionStrategy, Component, OnInit, computed, inject, input, signal } from '@angular/core';
import { NonNullableFormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { errorMessage } from '../shared/error-message';
import { ProductInput } from './product.model';
import { ProductService } from './product.service';

/** One component for both "new" (/products/new) and "edit" (/products/:id/edit). */
@Component({
  selector: 'app-product-form',
  imports: [ReactiveFormsModule, RouterLink],
  templateUrl: './product-form.html',
  styleUrl: './product-form.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProductForm implements OnInit {
  // Filled from the route parameter :id thanks to withComponentInputBinding() in app.config.ts
  readonly id = input<string>();

  private readonly fb = inject(NonNullableFormBuilder);
  private readonly productService = inject(ProductService);
  private readonly router = inject(Router);

  protected readonly isEdit = computed(() => !!this.id());
  protected readonly loading = signal(false);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);

  // Same rules as the API: name and numbers required, no negative price or stock
  protected readonly form = this.fb.group({
    name: ['', [Validators.required, Validators.maxLength(100)]],
    description: [''],
    price: [0, [Validators.required, Validators.min(0)]],
    stock: [0, [Validators.required, Validators.min(0), Validators.pattern(/^\d+$/)]],
  });

  ngOnInit(): void {
    const id = this.id();
    if (!id) {
      return;
    }
    this.loading.set(true);
    this.productService.get(Number(id)).subscribe({
      next: (p) => {
        this.form.setValue({ name: p.name, description: p.description ?? '', price: p.price, stock: p.stock });
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(errorMessage(err));
        this.loading.set(false);
      },
    });
  }

  protected showError(field: 'name' | 'price' | 'stock'): boolean {
    const control = this.form.controls[field];
    return control.invalid && (control.touched || control.dirty);
  }

  protected submit(): void {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    const value = this.form.getRawValue();
    const product: ProductInput = {
      name: value.name.trim(),
      description: value.description.trim() || null,
      price: Number(value.price),
      stock: Number(value.stock),
    };
    const id = this.id();
    const request = id ? this.productService.update(Number(id), product) : this.productService.create(product);

    this.saving.set(true);
    this.error.set(null);
    request.subscribe({
      next: () => this.router.navigate(['/products']),
      error: (err) => {
        this.error.set(errorMessage(err));
        this.saving.set(false);
      },
    });
  }
}
