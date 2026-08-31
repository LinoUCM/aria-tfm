import google.generativeai as genai
from core.config import settings

# Configura tu clave
genai.configure(api_key=settings.google_api_key)

print("Modelos de Embeddings disponibles:")
for m in genai.list_models():
    # Filtramos por los que tienen la capacidad de crear embeddings
    if 'embedContent' in m.supported_generation_methods:
        print(f"- Nombre: {m.name}")
        print(f"  Descripción: {m.description}\n")