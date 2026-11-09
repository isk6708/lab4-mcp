/** A product as returned by the FastAPI API (GET /products). */
export interface Product {
  id: number;
  name: string;
  description: string | null;
  price: number;
  stock: number;
}

/** What we send when creating or updating (POST / PUT /products). */
export type ProductInput = Omit<Product, 'id'>;
