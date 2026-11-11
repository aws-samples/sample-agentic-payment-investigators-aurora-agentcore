Seed data generators. The output files are large, so they go to an Amazon S3 assets bucket and never into git.

generate_corpus.py  writes the seed tables as TSV files under seeds/output/. The run is deterministic.
acceptance.py       checks the generated files against the data rules the workshop depends on. It needs no database and no AWS calls.
embed_corpus.py     fills the vector columns with Amazon Titan Text Embeddings V2 (1024 dimensions) and writes *_embedded.tsv files. Use --dry-run to count texts without AWS calls and --verify to check the embeddings.
load_seeds.py       loads the files into a database. Set the WORKSHOP_DB_* variables first.

Run them in that order. All names, cases and amounts are synthetic.
