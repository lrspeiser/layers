import Image from "next/image";
import styles from "./TractProductShelf.module.css";

export type TractShelfProduct = {
  id: string;
  surveyName: string;
  family: string;
  referenceBand: string;
  referenceUnit: string;
  productType: string;
  referenceImage?: string | null;
};

export function TractProductShelf({ tract, products }: { tract: number; products: TractShelfProduct[] }) {
  return (
    <div className={styles.shelf}>
      {products.map((product) => (
        <article key={product.id}>
          {product.referenceImage && <Image src={product.referenceImage} width={320} height={320} alt={`${product.surveyName} ${product.referenceBand} product in Rubin tract ${tract}`} />}
          <span>{product.family}</span>
          <h3>{product.surveyName}</h3>
          <p>{product.referenceBand} · {product.referenceUnit}</p>
          <small>{product.productType} · not yet in the aligned swipe</small>
        </article>
      ))}
    </div>
  );
}

export default TractProductShelf;
