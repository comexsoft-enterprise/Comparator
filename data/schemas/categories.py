from enum import Enum
from typing import Dict, List

from pydantic import BaseModel, Field, field_validator

class InternalType(str, Enum):
    FOOD = "Food"
    NON_FOOD = "Non-Food"

class InternalCategoryFood(str, Enum):
    OIL_SPICES_AND_SAUCES = "Oil, Spices, and Sauces"
    WATER_AND_SOFT_DRINKS = "Water and Soft Drinks"
    SNACKS = "Snacks"
    RICE_LEGUMES_AND_PASTA = "Rice, Legumes, and Pasta"
    SUGAR_CANDIES_AND_CHOCOLATE = "Sugar, Candies, and Chocolate"
    BABY_FOOD = "Baby Food"
    WINE_CELLAR_AND_LIQUOR = "Wine Cellar / Liquor"
    COCOA_COFFEE_AND_INFUSIONS = "Cocoa, Coffee, and Infusions"
    MEAT = "Meat"
    CEREALS_AND_BISCUITS = "Cereals and Biscuits"
    DELI_AND_CHEESES = "Deli and Cheeses"
    FROZEN_FOODS = "Frozen Foods"
    CANNED_FOODS_BROTHS_AND_CREAMS = "Canned Foods, Broths, and Creams"
    FRUIT_AND_VEGETABLES = "Fruit and Vegetables"
    EGGS_MILK_AND_BUTTER = "Eggs, Milk, and Butter"
    SEAFOOD_AND_FISH = "Seafood and Fish"
    PET_FOOD = "Pet Food"
    BAKERY_AND_PASTRY = "Bakery and Pastry"

class InternalCategoryNonFood(str, Enum):
    PERSONAL_CARE = "Personal Care"
    CLEANING_SUPPLIES = "Cleaning Supplies"
    HOUSEHOLD = "Household"
    PHARMACY = "Pharmacy"
    BABY_CARE = "Baby Care"
    APPLIANCES = "Appliances"
    ELECTRONICS = "Electronics"
    COMPUTING = "Computing"
    GAMING = "Gaming"
    TOYS = "Toys"
    CLOTHING = "Clothing"
    SPORTS = "Sports"
    TRAVEL_GEAR = "Travel Gear"
    SCHOOL_OFFICE_SUPPLIES = "School/Office Supplies"
    BOOKS = "Books"
    PET_SUPPLIES = "Pet Supplies"
    DIY = "DIY (Do-It-Yourself)"

# Non-Food Subcategories

class PersonalCare(str, Enum):
    HAIR_CARE = "Hair Care"
    BODY_CARE = "Body Care"
    MAKEUP = "Makeup"
    PERFUMERY = "Perfumery"
    SEXUAL_WELLNESS = "Sexual Wellness"
    WAXING_AND_SHAVING = "Waxing and Shaving"
    INTIMATE_CARE = "Intimate Care"
    ORAL_CARE = "Oral Care"

class CleaningSupplies(str, Enum):
    CLOTHES_CARE = "Clothes Care"
    PAPER_HYGIENE_AND_DISPOSABLES = "Paper, Hygiene, and Disposables"
    HOME_CLEANING = "Home Cleaning"
    KITCHEN_CLEANING = "Kitchen Cleaning"
    BATHROOM_CLEANING = "Bathroom Cleaning"
    INSECTICIDES_AND_AIR_FRESHENERS = "Insecticides and Air Fresheners"
    FOOD_CONSERVATION_AND_TABLEWARE = "Food Conservation and Tableware"
    SHOE_CARE = "Shoe Care"
    CLEANING_SUPPLIES_ACCESSORIES = "Cleaning Supplies Accessories"

class Household(str, Enum):
    LIGHTING_AND_BULBS = "Lighting and Bulbs"
    BATTERIES_AND_CHARGERS = "Batteries and Chargers"
    HOME_ORGANIZATION_AND_STORAGE = "Home Organization and Storage"
    TEXTILES_AND_DECORATION = "Textiles and Decoration"
    KITCHEN_UTENSILS_AND_COOKWARE = "Kitchen Utensils and Cookware"
    BATHROOM_ACCESSORIES = "Bathroom Accessories"
    HOME_REPAIR_AND_MAINTENANCE = "Home Repair and Maintenance"
    GARDENING_AND_OUTDOOR = "Gardening and Outdoor"

class Pharmacy(str, Enum):
    PHARMACY_PRODUCTS = "Pharmacy Products"
    DIETARY_SUPPLEMENTS = "Dietary Supplements"
    MEDICAL_DEVICES_AND_EQUIPMENT = "Medical Devices and Equipment"
    FIRST_AID_AND_WOUND_CARE = "First Aid and Wound Care"
    ALLERGY_AND_COLD_RELIEF = "Allergy and Cold Relief"
    PAIN_RELIEF_AND_FEVER = "Pain Relief and Fever"
    DIGESTIVE_HEALTH = "Digestive Health"
    VITAMINS_AND_MINERALS = "Vitamins and Minerals"
    SKIN_CARE_TREATMENTS = "Skin Care Treatments"
    EYE_CARE_AND_CONTACT_LENSES = "Eye Care and Contact Lenses"
    PERFUMES_AND_COSMETICS = "Perfumes and Cosmetics"
    BABY_CARE_PRODUCTS = "Baby Care Products"

class BabyCare(str, Enum):
    DIAPERS_AND_WIPES = "Diapers and Wipes"
    BABY_FEEDING = "Baby Feeding"
    BABY_HYGIENE = "Baby Hygiene"
    BABY_TOYS_AND_ACCESSORIES = "Baby Toys and Accessories"
    PREGNANCY_AND_MOTHERHOOD = "Pregnancy and Motherhood"

class Appliances(str, Enum):
    SMALL_KITCHEN_APPLIANCES = "Small Kitchen Appliances"
    LARGE_KITCHEN_APPLIANCES = "Large Kitchen Appliances"
    HOME_APPLIANCES = "Home Appliances"
    PERSONAL_CARE_APPLIANCES = "Personal Care Appliances"

class Electronics(str, Enum):
    AUDIO_AND_HEADPHONES = "Audio and Headphones"
    TELEVISIONS_AND_HOME_CINEMA = "Televisions and Home Cinema"
    CAMERAS_AND_CAMCORDERS = "Cameras and Camcorders"
    SMART_HOME_DEVICES = "Smart Home Devices"
    WEARABLE_TECHNOLOGY = "Wearable Technology"

class Computing(str, Enum):
    LAPTOPS_AND_TABLETS = "Laptops and Tablets"
    DESKTOP_COMPUTERS = "Desktop Computers"
    COMPUTER_COMPONENTS = "Computer Components"
    COMPUTER_ACCESSORIES = "Computer Accessories"
    SOFTWARE = "Software"

class Gaming(str, Enum):
    CONSOLES_AND_ACCESSORIES = "Consoles and Accessories"
    VIDEO_GAMES = "Video Games"
    GAMING_PERIPHERALS = "Gaming Peripherals"

class Toys(str, Enum):
    EDUCATIONAL_TOYS = "Educational Toys"
    ACTION_FIGURES_AND_DOLLS = "Action Figures and Dolls"
    PUZZLES_AND_BOARD_GAMES = "Puzzles and Board Games"
    OUTDOOR_AND_SPORTS_TOYS = "Outdoor and Sports Toys"
    REMOTE_CONTROLLED_TOYS = "Remote Controlled Toys"

class Clothing(str, Enum):
    MEN = "Men"
    WOMEN = "Women"
    CHILDREN = "Children"
    BABY = "Baby"

class Sports(str, Enum):
    FITNESS_AND_YOGA = "Fitness and Yoga"
    TEAM_SPORTS = "Team Sports"
    OUTDOOR_AND_ADVENTURE = "Outdoor and Adventure"
    CYCLING = "Cycling"
    SWIMMING = "Swimming"
    SPORTS_ACCESSORIES = "Sports Accessories"

class TravelGear(str, Enum):
    LUGGAGE_AND_SUITCASES = "Luggage and Suitcases"
    BACKPACKS_AND_DAYPACKS = "Backpacks and Daypacks"
    TRAVEL_ACCESSORIES = "Travel Accessories"
    TRAVEL_COMFORT_ITEMS = "Travel Comfort Items"

class SchoolOfficeSupplies(str, Enum):
    STATIONERY = "Stationery"
    OFFICE_SUPPLIES = "Office Supplies"
    ART_AND_CRAFT_MATERIALS = "Art and Craft Materials"
    SCHOOL_BAGS_AND_BACKPACKS = "School Bags and Backpacks"

class Books(str, Enum):
    FICTION = "Fiction"
    NON_FICTION = "Non-Fiction"
    CHILDREN_S_BOOKS = "Children's Books"
    ACADEMIC_AND_EDUCATIONAL = "Academic and Educational"
    COMICS_AND_GRAPHIC_NOVELS = "Comics and Graphic Novels"

class PetSupplies(str, Enum):
    CAT_SUPPLIES = "Cat Supplies"
    DOG_SUPPLIES = "Dog Supplies"
    SMALL_PETS = "Small Pets"
    BIRDS = "Birds"
    FISH_AND_AQUATIC_PETS = "Fish and Aquatic Pets"
    REPTILES = "Reptiles"
    PET_ACCESSORIES = "Pet Accessories"

class DIY(str, Enum):
    TOOLS_AND_EQUIPMENT = "Tools and Equipment"
    PAINTS_AND_CHEMICALS = "Paints and Chemicals"
    HARDWARE_AND_FIXTURES = "Hardware and Fixtures"
    PLUMBING_AND_ELECTRICAL = "Plumbing and Electrical"
    GARDENING_TOOLS_AND_SUPPLIES = "Gardening Tools and Supplies"


# Food Subcategories

class OilSpicesAndSauces(str, Enum):
    OIL_VINEGAR_AND_SALT = "Oil, Vinegar, and Salt"
    SPICES = "Spices"
    MAYONNAISE_KETCHUP_AND_MUSTARD = "Mayonnaise, Ketchup, and Mustard"
    OTHER_SAUCES = "Other Sauces"

class WaterAndSoftDrinks(str, Enum):
    WATER = "Water"
    ISOTONIC_AND_ENERGY_DRINKS = "Isotonic and Energy Drinks"
    COLA_SOFT_DRINK = "Cola Soft Drink"
    ORANGE_AND_LEMON_SOFT_DRINK = "Orange and Lemon Soft Drink"
    TONIC_AND_BITTER = "Tonic and Bitter"
    TEA_AND_STILL_SOFT_DRINKS = "Tea and Still Soft Drinks"

class Snacks(str, Enum):
    OLIVES_AND_PICKLES = "Olives and Pickles"
    NUTS_AND_DRIED_FRUIT = "Nuts and Dried Fruit"
    POTATO_CHIPS_AND_SNACKS = "Potato Chips and Snacks"

class RiceLegumesAndPasta(str, Enum):
    RICE = "Rice"
    LEGUMES = "Legumes"
    PASTA_AND_NOODLES = "Pasta and Noodles"

class SugarCandiesAndChocolate(str, Enum):
    SUGAR_AND_SWEETENER = "Sugar and Sweetener"
    GUM_AND_CANDIES = "Gum and Candies"
    CHOCOLATE = "Chocolate"
    SWEETS = "Sweets (Golosinas)"
    JAM_AND_HONEY = "Jam and Honey"
    NOUGAT = "Nougat (Turrones)"

class Baby(str, Enum):
    BABY_FOOD = "Baby Food"
    BOTTLE_AND_PACIFIER = "Bottle and Pacifier"
    HYGIENE_AND_CARE = "Hygiene and Care"
    WIPES_AND_DIAPERS = "Wipes and Diapers"

class WineCellarLiquor(str, Enum):
    BEER = "Beer"
    NON_ALCOHOLIC_BEER = "Non-Alcoholic Beer"
    LIQUORS = "Liquors"
    CIDER_AND_CAVA = "Cider and Cava (Sparkling Wine)"
    TINTO_DE_VERANO_AND_SANGRIA = "Tinto de Verano and Sangria"
    WHITE_WINE = "White Wine"
    LAMBRUSCO_AND_SPARKLING_WINE = "Lambrusco and Sparkling Wine"
    ROSE_WINE = "Rosé Wine"
    RED_WINE = "Red Wine"

class CocoaCoffeeAndInfusions(str, Enum):
    SOLUBLE_COCOA_AND_DRINKING_CHOCOLATE = "Soluble Cocoa and Drinking Chocolate"
    COFFEE_CAPSULES_AND_SINGLE_SERVE_PODS = "Coffee Capsules and Single-Serve Pods"
    GROUND_AND_WHOLE_BEAN_COFFEE = "Ground and Whole Bean Coffee"
    INSTANT_COFFEE_AND_OTHER_DRINKS = "Instant Coffee and Other Drinks"
    TEA_AND_INFUSIONS = "Tea and Infusions"

class Meat(str, Enum):
    MEAT_PREPARATIONS = "Meat Preparations (Arreglos)"
    POULTRY_AND_CHICKEN = "Poultry and Chicken"
    FROZEN_MEAT = "Frozen Meat"
    PORK = "Pork"
    RABBIT_AND_LAMB = "Rabbit and Lamb"
    CURED_MEATS = "Cured Meats (Embutido)"
    BURGERS_AND_MINCED_MEAT = "Burgers and Minced Meat"
    BEEF = "Beef"
    BREADED_AND_PREPARED_MEATS = "Breaded and Prepared Meats"

class CerealsAndBiscuits(str, Enum):
    CEREALS = "Cereals"
    BISCUITS = "Biscuits (Cookies)"
    PANCAKES = "Pancakes (Tortitas)"

class DeliAndCheeses(str, Enum):
    POULTRY_AND_COOKED_HAM = "Poultry and Cooked Ham"
    BACON_AND_SAUSAGES = "Bacon and Sausages"
    CHOPPED_MEAT_AND_MORTADELLA = "Chopped Meat and Mortadella"
    CURED_MEATS_CURADO = "Cured Meats (Embutido curado)"
    SERRANO_HAM = "Serrano Ham"
    PATE_AND_SOBRASADA = "Pâté and Sobrasada"
    CURED_SEMI_CURED_AND_SOFT_CHEESE = "Cured, Semi-cured, and Soft Cheese"
    SLICED_GRATED_AND_PORTIONED_CHEESE = "Sliced, Grated, and Portioned Cheese"
    SPREAD_AND_FRESH_CHEESE = "Spread and Fresh Cheese"

class FrozenFoods(str, Enum):
    RICE_AND_PASTA = "Rice and Pasta"
    MEAT = "Meat"
    ICE_CREAM = "Ice Cream"
    ICE = "Ice"
    SEAFOOD = "Seafood"
    FISH = "Fish"
    PIZZAS = "Pizzas"
    BREADED_PRODUCTS = "Breaded Products"
    CAKES_AND_CHURROS = "Cakes and Churros"
    VEGETABLES = "Vegetables"

class CannedFoodsBrothsAndCreams(str, Enum):
    TUNA_AND_OTHER_CANNED_FISH = "Tuna and Other Canned Fish"
    COCKLES_AND_MUSSELS = "Cockles and Mussels"
    CANNED_VEGETABLES_AND_FRUITS = "Canned Vegetables and Fruits"
    GAZPACHO_AND_CREAM_SOUPS = "Gazpacho and Cream Soups"
    SOUP_AND_BROTH = "Soup and Broth"
    TOMATO = "Tomato"

class HairCare(str, Enum):
    CONDITIONER_AND_HAIR_MASK = "Conditioner and Hair Mask"
    SHAMPOO = "Shampoo"
    HAIR_COLOR = "Hair Color"
    HAIR_STYLING = "Hair Styling"

class FacialAndBodyCare(str, Enum):
    SHAVING_AND_MENS_CARE = "Shaving and Men's Care"
    BODY_CARE = "Body Care"
    FACIAL_CARE_AND_HYGIENE = "Facial Care and Hygiene"
    HAIR_REMOVAL = "Hair Removal"
    DEODORANT = "Deodorant"
    GEL_AND_HAND_SOAP = "Gel and Hand Soap"
    ORAL_HYGIENE = "Oral Hygiene"
    INTIMATE_HYGIENE = "Intimate Hygiene"
    MANICURE_AND_PEDICURE = "Manicure and Pedicure"
    PERFUME_AND_COLOGNE = "Perfume and Cologne"
    SUNSCREEN_AND_AFTERSUN = "Sunscreen and Aftersun"

class PhytotherapyAndParapharmacy(str, Enum):
    PHYTOTHERAPY = "Phytotherapy"
    PARAPHARMACY = "Parapharmacy"

class FruitAndVegetables(str, Enum):
    FRUIT = "Fruit"
    LETTUCE_AND_PREPARED_SALAD = "Lettuce and Prepared Salad"
    VEGETABLES = "Vegetables"

class EggsMilkAndButter(str, Enum):
    EGGS = "Eggs"
    MILK_AND_VEGETABLE_DRINKS = "Milk and Vegetable Drinks"
    BUTTER_AND_MARGARINE = "Butter and Margarine"

class CleaningAndHome(str, Enum):
    DETERGENT_AND_FABRIC_SOFTENER = "Detergent and Fabric Softener"
    SCRUBBERS_CLOTHS_AND_GLOVES = "Scrubbers, Cloths, and Gloves"
    INSECTICIDE_AND_AIR_FRESHENER = "Insecticide and Air Freshener"
    BLEACH_AND_STRONG_LIQUIDS = "Bleach and Strong Liquids"
    GLASS_CLEANER = "Glass Cleaner"
    HOUSEHOLD_CLEANER_AND_FLOOR_CLEANER = "Household Cleaner and Floor Cleaner"
    BATHROOM_AND_TOILET_CLEANING = "Bathroom and Toilet Cleaning"
    KITCHEN_CLEANING = "Kitchen Cleaning"
    FURNITURE_AND_MULTIPURPOSE_CLEANER = "Furniture and Multipurpose Cleaner"
    DISH_CLEANING = "Dish Cleaning"
    TABLEWARE_AND_FOOD_PRESERVATION = "Tableware and Food Preservation"
    TOILET_PAPER_AND_CELLULOSE = "Toilet Paper and Cellulose"
    BATTERIES_AND_TRASH_BAGS = "Batteries and Trash Bags"
    CLEANING_AND_SHOE_UTENSILS = "Cleaning and Shoe Utensils"

class Makeup(str, Enum):
    MAKEUP_BASES_AND_CONCEALER = "Make-up Bases and Concealer"
    BLUSH_AND_POWDERS = "Blush and Powders"
    LIPS = "Lips"
    EYES = "Eyes"
    BRUSHES_AND_APPLICATORS = "Brushes and Applicators"

class SeafoodAndFish(str, Enum):
    SEAFOOD = "Seafood"
    FROZEN_FISH = "Frozen Fish"
    FRESH_FISH = "Fresh Fish"
    SALTED_AND_SMOKED_PRODUCTS = "Salted and Smoked Products"

class Pets(str, Enum):
    CAT = "Cat"
    DOG = "Dog"
    OTHER = "Other"

class BakeryAndPastry(str, Enum):
    OVEN_BAKED_PASTRIES = "Oven Baked Pastries"
    PACKAGED_PASTRIES = "Packaged Pastries"
    FLOUR_AND_BAKING_MIX = "Flour and Baking Mix"
    OVEN_BAKED_BREAD = "Oven Baked Bread"
    SLICED_BREAD_AND_OTHER_SPECIALTIES = "Sliced Bread and Other Specialties"
    TOASTED_AND_GRATED_BREAD = "Toasted and Grated Bread"
    BREADSTICKS_ROSQUILLETAS_AND_CROUTONS = "Breadsticks, Rosquilletas, and Croutons"
    CAKES_AND_PASTRIES = "Cakes and Pastries"
    CANDLES_AND_DECORATION = "Candles and Decoration"

class BabyFood(str, Enum):
    BABY_FOOD = "Baby Food"

class PetFood(str, Enum):
    PET_FOOD = "Pet Food"


class ProductCategorization(BaseModel):
    """Model for product categorization with type, category, and subcategory."""
    
    product_name: str = Field(..., description="Name of the product")
    type: str = Field(..., description="Product type: Food or Non-Food")
    category: str = Field(..., description="Product category")
    subcategory: str = Field(..., description="Product subcategory")
    
    @field_validator('type')
    @classmethod
    def validate_type(cls, v):
        """Validate that type is a valid InternalType."""
        valid_types = [t.value for t in InternalType]
        if v not in valid_types:
            raise ValueError(f"Type must be one of: {valid_types}")
        return v
    
    @field_validator('category')
    @classmethod
    def validate_category(cls, v, info):
        """Validate category matches the type."""
        type_value = info.data.get('type')
        
        if type_value == InternalType.FOOD.value:
            valid_categories = [c.value for c in InternalCategoryFood]
        elif type_value == InternalType.NON_FOOD.value:
            valid_categories = [c.value for c in InternalCategoryNonFood]
        else:
            raise ValueError(f"Unknown type: {type_value}")
        
        if v not in valid_categories:
            raise ValueError(f"Category '{v}' is not valid for type '{type_value}'. Valid options: {valid_categories}")
        
        return v
    
    @field_validator('subcategory')
    @classmethod
    def validate_subcategory(cls, v, info):
        """Validate subcategory matches the category."""
        if v is None:
            return v
        
        category = info.data.get('category')
        
        # Get valid subcategories for the category
        valid_subcategories = get_valid_subcategories_for_category(category)
        
        if v not in valid_subcategories:
            raise ValueError(
                f"Subcategory '{v}' is not valid for category '{category}'. "
                f"Valid options: {valid_subcategories}"
            )
        
        return v
    
def get_category_subcategory_map() -> Dict[str, type]:
    """Map category names to their subcategory enum classes."""
    return {
        InternalCategoryFood.OIL_SPICES_AND_SAUCES.value: OilSpicesAndSauces,
        InternalCategoryFood.WATER_AND_SOFT_DRINKS.value: WaterAndSoftDrinks,
        InternalCategoryFood.SNACKS.value: Snacks,
        InternalCategoryFood.RICE_LEGUMES_AND_PASTA.value: RiceLegumesAndPasta,
        InternalCategoryFood.SUGAR_CANDIES_AND_CHOCOLATE.value: SugarCandiesAndChocolate,
        InternalCategoryFood.BABY_FOOD.value: BabyFood,
        InternalCategoryFood.WINE_CELLAR_AND_LIQUOR.value: WineCellarLiquor,
        InternalCategoryFood.COCOA_COFFEE_AND_INFUSIONS.value: CocoaCoffeeAndInfusions,
        InternalCategoryFood.MEAT.value: Meat,
        InternalCategoryFood.CEREALS_AND_BISCUITS.value: CerealsAndBiscuits,
        InternalCategoryFood.DELI_AND_CHEESES.value: DeliAndCheeses,
        InternalCategoryFood.FROZEN_FOODS.value: FrozenFoods,
        InternalCategoryFood.CANNED_FOODS_BROTHS_AND_CREAMS.value: CannedFoodsBrothsAndCreams,
        InternalCategoryFood.FRUIT_AND_VEGETABLES.value: FruitAndVegetables,
        InternalCategoryFood.EGGS_MILK_AND_BUTTER.value: EggsMilkAndButter,
        InternalCategoryFood.SEAFOOD_AND_FISH.value: SeafoodAndFish,
        InternalCategoryFood.PET_FOOD.value: PetFood,
        InternalCategoryFood.BAKERY_AND_PASTRY.value: BakeryAndPastry,
        InternalCategoryNonFood.PERSONAL_CARE.value: PersonalCare,
        InternalCategoryNonFood.CLEANING_SUPPLIES.value: CleaningSupplies,
        InternalCategoryNonFood.HOUSEHOLD.value: Household,
        InternalCategoryNonFood.PHARMACY.value: Pharmacy,
        InternalCategoryNonFood.BABY_CARE.value: BabyCare,
        InternalCategoryNonFood.APPLIANCES.value: Appliances,
        InternalCategoryNonFood.ELECTRONICS.value: Electronics,
        InternalCategoryNonFood.COMPUTING.value: Computing,
        InternalCategoryNonFood.GAMING.value: Gaming,
        InternalCategoryNonFood.TOYS.value: Toys,
        InternalCategoryNonFood.CLOTHING.value: Clothing,
        InternalCategoryNonFood.SPORTS.value: Sports,
        InternalCategoryNonFood.TRAVEL_GEAR.value: TravelGear,
        InternalCategoryNonFood.SCHOOL_OFFICE_SUPPLIES.value: SchoolOfficeSupplies,
        InternalCategoryNonFood.BOOKS.value: Books,
        InternalCategoryNonFood.PET_SUPPLIES.value: PetSupplies,
        InternalCategoryNonFood.DIY.value: DIY,
    }

def get_valid_subcategories_for_category(category: str) -> List[str]:
    """Get list of valid subcategory values for a given category."""
    category_map = get_category_subcategory_map()
    subcategory_enum = category_map.get(category)
    
    if subcategory_enum:
        return [sc.value for sc in subcategory_enum]
    return []

def get_all_subcategories() -> List[str]:
    """Get all valid subcategory values across all categories."""
    category_map = get_category_subcategory_map()
    all_subcats = []
    
    for subcat_enum in category_map.values():
        all_subcats.extend([sc.value for sc in subcat_enum])
    
    return all_subcats

def get_all_subcategories() -> List[str]:
    """Get all valid subcategory values across all categories."""
    category_map = get_category_subcategory_map()
    all_subcats = []
    
    for subcat_enum in category_map.values():
        all_subcats.extend([sc.value for sc in subcat_enum])
    
    return all_subcats
