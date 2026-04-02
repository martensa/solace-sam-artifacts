-- ============================================================================
-- CONTRACT MANAGEMENT DATABASE
-- Framework Contract Database for Solace Agent Mesh Demo
-- AI-Powered Article Recognition & Market Price Comparison
-- ============================================================================
-- Version: 1.0
-- Date:    2026-03-15
-- Target:  PostgreSQL 14+
-- Database: contract_management
-- Schema:  contracts (SET search_path TO contracts;)
-- Domain:  Facility Management / Procurement
-- Scope:   HVAC, Sanitary, Electrical, Office Supplies, Tools & Accessories
-- Data:    15 suppliers, 50 articles (real EAN-13), 15 framework contracts,
--          150 contract line items, 118 volume discount tiers
-- Language: English (no special characters)
-- Currency: EUR
-- Docker:  Place in /docker-entrypoint-initdb.d/ for auto-initialization
--
-- Key Functions:
--   search_articles(text)           - Fuzzy search by EAN, name, brand, description
--   get_best_price(article_id, qty) - Best price incl. volume tiers
--   recommend_order(article_id, qty) - Smart order recommendation with tier optimization
--
-- Key Views:
--   v_best_contract_price   - Ranked prices per article across contracts
--   v_article_search        - Article catalog with best prices & savings
--   v_supplier_article_matrix - Which supplier offers what at which price
-- ============================================================================

-- ----------------------------------------------------------------------------
-- DATABASE CREATION (for Docker initdb.d or manual setup)
-- Note: CREATE DATABASE cannot run inside a transaction block.
-- ----------------------------------------------------------------------------
CREATE DATABASE "contract_management" OWNER postgres;
\c contract_management

BEGIN;

-- ============================================================================
-- 1. SCHEMA SETUP
-- ============================================================================
DROP SCHEMA IF EXISTS contracts CASCADE;
CREATE SCHEMA contracts;
SET search_path TO contracts;

-- ============================================================================
-- 2. TABLE DEFINITIONS
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 2.1 CATEGORIES
-- ----------------------------------------------------------------------------
CREATE TABLE categories (
    category_id   SERIAL PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL UNIQUE,
    description   TEXT
);

-- ----------------------------------------------------------------------------
-- 2.2 SUPPLIERS
-- ----------------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id       SERIAL PRIMARY KEY,
    supplier_name     VARCHAR(200) NOT NULL,
    contact_person    VARCHAR(150),
    email             VARCHAR(200),
    phone             VARCHAR(50),
    street            VARCHAR(200),
    postal_code       VARCHAR(20),
    city              VARCHAR(100),
    country           VARCHAR(100) DEFAULT 'Germany',
    website           VARCHAR(300),
    shop_url          VARCHAR(300),
    payment_terms     VARCHAR(200),
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 2.3 ARTICLES (Product Catalog)
-- ----------------------------------------------------------------------------
CREATE TABLE articles (
    article_id        SERIAL PRIMARY KEY,
    ean               VARCHAR(13) NOT NULL UNIQUE,
    article_name      VARCHAR(300) NOT NULL,
    brand             VARCHAR(100),
    manufacturer      VARCHAR(200),
    description       TEXT,
    category_id       INT NOT NULL REFERENCES categories(category_id),
    unit              VARCHAR(30) DEFAULT 'piece',
    weight_kg         NUMERIC(10,3),
    is_active         BOOLEAN DEFAULT TRUE,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_articles_ean ON articles(ean);
CREATE INDEX idx_articles_name ON articles(article_name);
CREATE INDEX idx_articles_category ON articles(category_id);

-- ----------------------------------------------------------------------------
-- 2.4 CONTRACTS (Framework Agreements)
-- ----------------------------------------------------------------------------
CREATE TABLE contracts (
    contract_id       SERIAL PRIMARY KEY,
    contract_number   VARCHAR(50) NOT NULL UNIQUE,
    supplier_id       INT NOT NULL REFERENCES suppliers(supplier_id),
    title             VARCHAR(300) NOT NULL,
    version           VARCHAR(20) DEFAULT '1.0',
    status            VARCHAR(30) DEFAULT 'active'
                      CHECK (status IN ('active','expired','draft','terminated')),
    valid_from        DATE NOT NULL,
    valid_until       DATE NOT NULL,
    payment_terms     VARCHAR(200),
    delivery_terms    VARCHAR(200),
    currency          VARCHAR(3) DEFAULT 'EUR',
    minimum_order_value NUMERIC(10,2),
    free_shipping_threshold NUMERIC(10,2),
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_contract_dates CHECK (valid_until > valid_from)
);

-- ----------------------------------------------------------------------------
-- 2.5 CONTRACT ARTICLES (Contract Line Items)
-- ----------------------------------------------------------------------------
CREATE TABLE contract_articles (
    contract_article_id SERIAL PRIMARY KEY,
    contract_id         INT NOT NULL REFERENCES contracts(contract_id),
    article_id          INT NOT NULL REFERENCES articles(article_id),
    contract_price      NUMERIC(10,2) NOT NULL,
    list_price          NUMERIC(10,2),
    discount_pct        NUMERIC(5,2),
    min_order_qty       INT DEFAULT 1,
    delivery_days       INT DEFAULT 5,
    is_preferred        BOOLEAN DEFAULT FALSE,
    notes               TEXT,
    CONSTRAINT uq_contract_article UNIQUE (contract_id, article_id)
);

-- ----------------------------------------------------------------------------
-- 2.6 TIERED PRICING (Volume Discounts)
-- ----------------------------------------------------------------------------
CREATE TABLE tiered_pricing (
    tier_id             SERIAL PRIMARY KEY,
    contract_article_id INT NOT NULL REFERENCES contract_articles(contract_article_id),
    min_quantity        INT NOT NULL,
    max_quantity        INT,
    tier_price          NUMERIC(10,2) NOT NULL,
    discount_pct        NUMERIC(5,2),
    CONSTRAINT chk_tier_qty CHECK (max_quantity IS NULL OR max_quantity >= min_quantity)
);

-- ============================================================================
-- 3. REFERENCE DATA
-- ============================================================================

INSERT INTO categories (category_name, description) VALUES
('Office Supplies',            'Pens, markers, adhesives, correction fluid, folders, notes'),
('HVAC',                       'Heating, ventilation and air conditioning components'),
('Electrical',                 'Switches, circuit breakers, LED lighting, connectors, cables'),
('Sanitary',                   'Faucets, fittings, bathroom accessories, pipes, siphons'),
('Tools and Accessories',      'Hand tools, power tool accessories, fasteners, toolboxes');

-- ============================================================================
-- 4. SUPPLIERS
-- ============================================================================

INSERT INTO suppliers (supplier_id, supplier_name, contact_person, email, phone, street, postal_code, city, country, website, shop_url, payment_terms, notes) VALUES
( 1, 'Office Direct GmbH',        'Thomas Weber',     'weber@officedirect.de',       '+49 89 1234 5670',  'Leopoldstrasse 42',       '80802', 'Munich',        'Germany', 'https://www.officedirect.de',       'https://shop.officedirect.de',       'Net 30 days',           'Specialist for office supplies and stationery'),
( 2, 'HausTechnik Mueller AG',    'Klaus Mueller',    'mueller@haustechnik-m.de',    '+49 711 9876 5430', 'Industriestrasse 15',     '70565', 'Stuttgart',     'Germany', 'https://www.haustechnik-mueller.de','https://shop.haustechnik-mueller.de','Net 30 days',           'HVAC specialist with 25 years experience'),
( 3, 'Elektro Schmitt GmbH',      'Petra Schmitt',    'schmitt@elektro-schmitt.de',  '+49 69 5555 1234',  'Mainzer Landstrasse 88',  '60329', 'Frankfurt',     'Germany', 'https://www.elektro-schmitt.de',   'https://shop.elektro-schmitt.de',   'Net 14 days',           'Electrical installations and components'),
( 4, 'SanProfi GmbH',             'Andrea Fischer',   'fischer@sanprofi.de',         '+49 221 7777 8880', 'Koelner Strasse 23',      '50674', 'Cologne',       'Germany', 'https://www.sanprofi.de',          'https://shop.sanprofi.de',          'Net 30 days',           'Sanitary wholesale for professional installers'),
( 5, 'Werkzeug Wagner KG',        'Martin Wagner',    'wagner@werkzeug-wagner.de',   '+49 30 4444 5560',  'Berliner Allee 99',       '13088', 'Berlin',        'Germany', 'https://www.werkzeug-wagner.de',   'https://shop.werkzeug-wagner.de',   'Net 30 days',           'Professional tools and hardware'),
( 6, 'AllRound Supply GmbH',      'Sabine Klein',     'klein@allround-supply.de',    '+49 40 3333 2220',  'Hamburger Strasse 55',    '22083', 'Hamburg',       'Germany', 'https://www.allround-supply.de',   'https://shop.allround-supply.de',   'Net 45 days',           'Multi-category supplier for facility management'),
( 7, 'TechnoTherm GmbH',          'Ralf Becker',      'becker@technotherm.de',       '+49 351 6666 7770', 'Dresdner Strasse 12',     '01069', 'Dresden',       'Germany', 'https://www.technotherm.de',       'https://shop.technotherm.de',       'Net 30 days',           'HVAC and electrical technology'),
( 8, 'BauBedarf Braun AG',        'Frank Braun',      'braun@baubedarf-braun.de',    '+49 511 8888 9990', 'Hannoversche Strasse 77', '30159', 'Hanover',       'Germany', 'https://www.baubedarf-braun.de',   'https://shop.baubedarf-braun.de',   'Net 30 days, 2% early payment discount 10 days', 'Construction supplies and building materials'),
( 9, 'ProOffice Solutions GmbH',   'Julia Hofmann',    'hofmann@prooffice.de',        '+49 89 2222 3330',  'Nymphenburger Str 120',   '80636', 'Munich',        'Germany', 'https://www.prooffice.de',         'https://shop.prooffice.de',         'Net 30 days',           'Premium office supplies for enterprises'),
(10, 'KlimaTech GmbH',            'Uwe Richter',      'richter@klimatech.de',        '+49 201 1111 4440', 'Essener Strasse 33',      '45127', 'Essen',         'Germany', 'https://www.klimatech.de',         'https://shop.klimatech.de',         'Net 30 days',           'Climate control and ventilation systems'),
(11, 'Sanitaer Express GmbH',     'Monika Hartmann',  'hartmann@sanitaer-express.de','+49 231 5555 6660', 'Dortmunder Weg 18',       '44135', 'Dortmund',      'Germany', 'https://www.sanitaer-express.de',  'https://shop.sanitaer-express.de',  'Net 14 days',           'Fast delivery sanitary products'),
(12, 'ElektroProfi AG',           'Stefan Lang',      'lang@elektroprofi.de',        '+49 911 7777 1110', 'Nuernberger Strasse 44',  '90402', 'Nuremberg',     'Germany', 'https://www.elektroprofi.de',      'https://shop.elektroprofi.de',      'Net 30 days',           'Electrical and HVAC components'),
(13, 'MegaTool GmbH',             'Bernd Schwarz',    'schwarz@megatool.de',         '+49 621 9999 8880', 'Mannheimer Ring 7',       '68161', 'Mannheim',      'Germany', 'https://www.megatool.de',          'https://shop.megatool.de',          'Net 30 days',           'Tools, fasteners and workshop equipment'),
(14, 'Universal Supplies AG',     'Heike Krause',     'krause@universal-supplies.de','+49 341 4444 3330', 'Leipziger Platz 5',       '04109', 'Leipzig',       'Germany', 'https://www.universal-supplies.de','https://shop.universal-supplies.de','Net 45 days, 3% early payment discount 14 days', 'Cross-category industrial supplies'),
(15, 'FacilityPro GmbH',          'Dirk Neumann',     'neumann@facilitypro.de',      '+49 211 6666 5550', 'Duesseldorfer Str 101',   '40215', 'Duesseldorf',   'Germany', 'https://www.facilitypro.de',       'https://shop.facilitypro.de',       'Net 30 days',           'Complete facility management supplier');

SELECT setval('suppliers_supplier_id_seq', 15);

-- ============================================================================
-- 5. ARTICLES (Real products with realistic EAN numbers)
-- ============================================================================

INSERT INTO articles (article_id, ean, article_name, brand, manufacturer, description, category_id, unit, weight_kg) VALUES
-- -------------------------------------------------------------------------
-- OFFICE SUPPLIES (category_id = 1)
-- -------------------------------------------------------------------------
( 1, '4042448835819', 'tesa Film Standard 15mm x 66m',                      'tesa',        'tesa SE',                         'Transparent adhesive tape, standard quality, 15mm wide, 66m roll, for everyday office use', 1, 'roll',    0.060),
( 2, '4001895551048', 'Post-it Notes 654 Yellow 76x76mm 100 Sheets',        'Post-it',     '3M Deutschland GmbH',             'Self-adhesive notes in canary yellow, 76x76mm, 100 sheets per pad, repositionable', 1, 'pad',     0.045),
( 3, '4002432397044', 'Leitz 1010 Plastic Lever Arch File 80mm A4 Blue',    'Leitz',       'Esselte Leitz GmbH',              'Standard plastic lever arch file, 80mm spine width, A4 format, blue, with finger hole', 1, 'piece',   0.450),
( 4, '4006381333627', 'STABILO BOSS Original Highlighter Yellow',           'STABILO',     'STABILO International GmbH',      'Fluorescent highlighter in yellow, wedge tip 2-5mm, anti-dry-out ink, up to 4 hours cap-off time', 1, 'piece',   0.015),
( 5, '4004764012879', 'edding 3000 Permanent Marker Black Round Tip',       'edding',      'edding International GmbH',       'Permanent marker with round tip 1.5-3mm, waterproof ink, quick-drying, black', 1, 'piece',   0.018),
( 6, '4015000094191', 'Pritt Glue Stick 43g',                               'Pritt',       'Henkel AG',                       'Solvent-free glue stick, 43g, washes out of most textiles, child-safe, for paper, cardboard, photos', 1, 'piece',   0.055),
( 7, '4026700450507', 'UHU All Purpose Adhesive 125g Tube',                 'UHU',         'UHU GmbH & Co. KG',              'Universal adhesive, transparent when dry, bonds paper, wood, textile, metal, glass, ceramic, 125g tube', 1, 'tube',    0.150),
( 8, '3086126895053', 'Tipp-Ex Rapid Correction Fluid 20ml',                'Tipp-Ex',     'BIC Deutschland GmbH',            'Quick-drying correction fluid with foam applicator, 20ml bottle, white, for precise corrections', 1, 'piece',   0.040),
( 9, '3086120100032', 'BIC Cristal Original Ballpoint Pen Blue Medium',     'BIC',         'BIC Deutschland GmbH',            'Classic ballpoint pen, crystal clear barrel, medium point 1.0mm, blue ink, cap with ventilation', 1, 'piece',   0.006),
(10, '4005401484301', 'Faber-Castell Grip 2001 Pencil HB',                  'Faber-Castell','Faber-Castell AG',                'Ergonomic triangular pencil, patented soft-grip zone, break-resistant lead, HB grade', 1, 'piece',   0.008),

-- -------------------------------------------------------------------------
-- HVAC (category_id = 2)
-- -------------------------------------------------------------------------
(11, '5702424626341', 'Danfoss RA-N 15 Thermostatic Valve Body DN15',       'Danfoss',     'Danfoss A/S',                     'Thermostatic radiator valve body, DN15 1/2 inch, angle pattern, presettable, for 2-pipe systems', 2, 'piece',   0.320),
(12, '5702424628086', 'Danfoss RAE-K 5034 Thermostatic Sensor Element',     'Danfoss',     'Danfoss A/S',                     'Thermostatic sensor with built-in sensor, snap connection, temperature range 8-28C, frost protection', 2, 'piece',   0.120),
(13, '4026755114737', 'Oventrop AV9 Thermostatic Valve DN15 Angle',         'Oventrop',    'Oventrop GmbH & Co. KG',          'Thermostatic valve body AV9, DN15, angle form, presettable, M30x1.5 connection', 2, 'piece',   0.290),
(14, '4026755326895', 'Oventrop Uni LH Thermostat Head M30x1.5',            'Oventrop',    'Oventrop GmbH & Co. KG',          'Thermostat head with liquid sensor, M30x1.5 thread, setting range 7-28C, with zero position', 2, 'piece',   0.095),
(15, '4012799541011', 'Honeywell Home Rondostat HR20 Radiator Controller',  'Honeywell',   'Resideo Technologies Inc',        'Programmable electronic thermostatic radiator controller, digital display, individual time programs', 2, 'piece',   0.190),
(16, '5710627042319', 'Grundfos Alpha2 25-60 180 Circulation Pump',         'Grundfos',    'Grundfos GmbH',                   'High-efficiency circulation pump, energy class A, AUTOADAPT function, 180mm installation length', 2, 'piece',   2.100),
(17, '4024749113438', 'Viega Profipress Elbow 90 Degree 22mm Copper',       'Viega',       'Viega GmbH & Co. KG',             'Press fitting elbow 90 degrees, 22mm copper, SC-Contur, for heating and cooling systems', 2, 'piece',   0.085),
(18, '4026755393309', 'Oventrop Multiblock T Connection Set Angular',        'Oventrop',    'Oventrop GmbH & Co. KG',          'Connection set for radiators, angular form, with thermostatic valve and lockshield, chrome plated', 2, 'piece',   0.480),
(19, '4048164600380', 'Wilo Star-Z 15 TT Drinking Water Circulation Pump', 'Wilo',        'Wilo SE',                         'Circulation pump for drinking water systems, timer function, thermal disinfection, brass housing', 2, 'piece',   1.800),
(20, '4033032002013', 'Zehnder Charleston 2056 Design Radiator White',      'Zehnder',     'Zehnder Group Deutschland GmbH',  'Steel tubular radiator, 2-column, height 558mm, RAL 9016 traffic white, lateral connection', 2, 'piece',  12.500),

-- -------------------------------------------------------------------------
-- ELECTRICAL (category_id = 3)
-- -------------------------------------------------------------------------
(21, '4011395179000', 'Busch-Jaeger Reflex SI Socket Outlet alpine white',  'Busch-Jaeger','Busch-Jaeger Elektro GmbH',       'Socket outlet with child protection, flush-mounted, alpine white, Reflex SI design line', 3, 'piece',   0.075),
(22, '4011395176009', 'Busch-Jaeger Reflex SI Light Switch alpine white',   'Busch-Jaeger','Busch-Jaeger Elektro GmbH',       'Rocker switch, flush-mounted, universal switch, alpine white, Reflex SI design, 10AX/250V', 3, 'piece',   0.055),
(23, '3250615730183', 'Hager MBN116 Miniature Circuit Breaker B16A',        'Hager',       'Hager Vertriebsgesellschaft mbH', 'MCB 1-pole, B-characteristic, 16A, 6kA breaking capacity, DIN rail mounting, 18mm width', 3, 'piece',   0.110),
(24, '4007123631100', 'Brennenstuhl Premium-Line 6-Way Power Strip 3m',     'Brennenstuhl','Hugo Brennenstuhl GmbH & Co. KG', 'Power strip with 6 sockets, switch, 3m cable H05VV-F 3G1.5, safety socket, 230V/16A', 3, 'piece',   0.780),
(25, '4058075127029', 'OSRAM LED Star Classic A60 9W E27 2700K',            'OSRAM',       'LEDVANCE GmbH',                   'LED bulb, pear shape, 9W replaces 60W, 806 lumen, warm white 2700K, E27 base, not dimmable', 3, 'piece',   0.036),
(26, '8718699630546', 'Philips LED Classic 7W E27 WarmGlow Dimmable',       'Philips',     'Signify Netherlands B.V.',        'LED bulb, warm glow dimming effect, 7W replaces 60W, 806 lumen, E27 base, clear glass', 3, 'piece',   0.030),
(27, '4044918365093', 'WAGO 221-413 Lever Connector 3-Way 0.14-4mm2',      'WAGO',        'WAGO GmbH & Co. KG',             'Compact splicing connector, 3 conductors, transparent housing, lever operation, 0.14-4mm2, 450V', 3, 'pack',    0.005),
(28, '4007123596898', 'Brennenstuhl Solar LED Wall Light SOL 800',          'Brennenstuhl','Hugo Brennenstuhl GmbH & Co. KG', 'Solar powered LED wall light with motion detector, 400 lumen, IP44, battery included', 3, 'piece',   0.480),
(29, '4011395211601', 'Busch-Jaeger Reflex SI Double Socket alpine white',  'Busch-Jaeger','Busch-Jaeger Elektro GmbH',       'Double socket outlet with child protection, horizontal, alpine white, flush-mounted', 3, 'piece',   0.145),
(30, '3250615730268', 'Hager MBN316 MCB 3-pole B16A',                       'Hager',       'Hager Vertriebsgesellschaft mbH', 'Miniature circuit breaker 3-pole, B-characteristic, 16A, 6kA, DIN rail mounting', 3, 'piece',   0.315),

-- -------------------------------------------------------------------------
-- SANITARY (category_id = 4)
-- -------------------------------------------------------------------------
(31, '4005176934520', 'Grohe Eurosmart Single-Lever Basin Mixer M-Size',    'Grohe',       'Grohe AG',                        'Single-lever basin mixer, M-size, SilkMove ceramic cartridge, EcoJoy water saving, with pop-up waste, chrome', 4, 'piece',   1.450),
(32, '4005176532085', 'Grohe BauEdge Single-Lever Basin Mixer S-Size',      'Grohe',       'Grohe AG',                        'Single-lever basin mixer, S-size, SilkMove ceramic cartridge, DN15 connection, with pop-up waste, chrome', 4, 'piece',   1.200),
(33, '4005176905506', 'Grohe Contemporary Soap Dispenser Chrome',           'Grohe',       'Grohe AG',                        'Wall-mounted soap dispenser, chrome finish, 400ml capacity, for liquid soap, concealed fixings', 4, 'piece',   0.650),
(34, '4011097789118', 'Hansgrohe Logis Single-Lever Basin Mixer 100',       'Hansgrohe',   'Hansgrohe SE',                    'Single-lever basin mixer, ComfortZone 100, with pop-up waste, chrome, EcoSmart 5 l/min', 4, 'piece',   1.350),
(35, '4011097751795', 'Hansgrohe Croma Select S Shower Set Vario',          'Hansgrohe',   'Hansgrohe SE',                    'Hand shower set with Croma Select S Vario, shower holder, 160cm hose, Select button, 3 spray modes', 4, 'piece',   0.480),
(36, '4025416380290', 'Geberit Sigma20 Flush Plate White/Chrome',           'Geberit',     'Geberit Vertriebs GmbH',          'Dual-flush actuator plate, for Sigma concealed cistern, white with chrome-plated buttons', 4, 'piece',   0.320),
(37, '4025416556111', 'Geberit Bottle Trap for Washbasin DN32',             'Geberit',     'Geberit Vertriebs GmbH',          'Bottle trap with dip tube, space-saving design, DN32 connection, white plastic, adjustable height', 4, 'piece',   0.190),
(38, '4008838221327', 'WENKO Bosio Toilet Paper Holder Stainless Steel',    'WENKO',       'Wenko-Wenselaar GmbH & Co. KG',  'Toilet paper holder without lid, brushed stainless steel, wall-mounted, Turbo-Loc fixing without drilling', 4, 'piece',   0.280),
(39, '4008838271872', 'WENKO Bosio Soap Dispenser Stainless Steel',         'WENKO',       'Wenko-Wenselaar GmbH & Co. KG',  'Soap dispenser, brushed stainless steel, 200ml capacity, wall-mounted, Turbo-Loc fixing', 4, 'piece',   0.310),
(40, '4005176450235', 'Grohe Essentials Towel Ring Chrome',                 'Grohe',       'Grohe AG',                        'Towel ring, wall-mounted, chrome finish, concealed fixings, Grohe StarLight surface', 4, 'piece',   0.350),

-- -------------------------------------------------------------------------
-- TOOLS AND ACCESSORIES (category_id = 5)
-- -------------------------------------------------------------------------
(41, '4003773066316', 'Knipex Cobra Water Pump Pliers 250mm',               'Knipex',      'KNIPEX-Werk C. Gustav Putsch KG', 'High-tech water pump pliers, push-button adjustment, 25 positions, chrome vanadium steel, 250mm', 5, 'piece',   0.315),
(42, '4003773078012', 'Knipex Combination Pliers 200mm',                    'Knipex',      'KNIPEX-Werk C. Gustav Putsch KG', 'Combination pliers, cutting edges for hard wire, chrome vanadium steel, multi-component grips, 200mm', 5, 'piece',   0.260),
(43, '4013288163585', 'Wera Kraftform Kompakt 25 Screwdriver Set',          'Wera',        'Wera Werkzeuge GmbH',             'Compact screwdriver bit set, 25 pieces, with handle, PH/PZ/TX/SL bits, magnetic bit holder', 5, 'piece',   0.290),
(44, '4013288167040', 'Wera 950 SPKL/9 SM N Hex Key Set Metric',           'Wera',        'Wera Werkzeuge GmbH',             'L-key set, 9 pieces, hex-plus profile, 1.5-10mm, multicolour, ball end, holding function', 5, 'set',     0.240),
(45, '3253560739003', 'Stanley FatMax Tape Measure 5m x 32mm',              'Stanley',     'Stanley Black & Decker Inc',      'Tape measure with Blade Armor coated blade, 5m length, 32mm wide blade, magnetic hook, mylar coating', 5, 'piece',   0.410),
(46, '3253560162351', 'Stanley Sortmaster Organizer 43cm',                  'Stanley',     'Stanley Black & Decker Inc',      'Stackable organizer with adjustable compartments, transparent lid, water-sealed, 43x33x9cm', 5, 'piece',   1.200),
(47, '4048962194906', 'Fischer DuoPower 8x40 Wall Plugs Pack of 100',      'Fischer',     'fischerwerke GmbH & Co. KG',      'Universal wall plug for all building materials, intelligent 2-component design, 8mm diameter, 40mm length', 5, 'pack',    0.380),
(48, '4048962193800', 'Fischer DuoPower 6x30 Wall Plugs Pack of 100',      'Fischer',     'fischerwerke GmbH & Co. KG',      'Universal wall plug 2-component, 6mm diameter, 30mm length, for solid and hollow materials', 5, 'pack',    0.200),
(49, '3165140853682', 'Bosch HSS-TiN Metal Drill Bit Set 19 Pieces',       'Bosch',       'Robert Bosch GmbH',               'HSS twist drill bit set, titanium nitride coated, 1-10mm in 0.5mm steps, for steel, non-ferrous metals', 5, 'set',     0.450),
(50, '4007123398287', 'Brennenstuhl Professional LED Work Light 50W',       'Brennenstuhl','Hugo Brennenstuhl GmbH & Co. KG', 'Portable LED work light, 50W, 4750 lumen, IP65, 5m cable, daylight 6500K, foldable stand', 5, 'piece',   2.100);

SELECT setval('articles_article_id_seq', 50);

-- ============================================================================
-- 6. CONTRACTS
-- ============================================================================

INSERT INTO contracts (contract_id, contract_number, supplier_id, title, version, status, valid_from, valid_until, payment_terms, delivery_terms, currency, minimum_order_value, free_shipping_threshold, notes) VALUES
( 1, 'FC-2025-001', 1, 'Framework Agreement Office Supplies 2025',             '2.1', 'active', '2025-01-01', '2026-12-31', 'Net 30 days',                       'DDP, free delivery above 100 EUR', 'EUR',  25.00,  100.00, 'Annual renewal option'),
( 2, 'FC-2025-002', 2, 'Framework Agreement HVAC Components 2025',             '1.0', 'active', '2025-04-01', '2027-03-31', 'Net 30 days',                       'FCA warehouse Stuttgart',          'EUR',  50.00,  250.00, '2-year agreement'),
( 3, 'FC-2025-003', 3, 'Framework Agreement Electrical Installations 2025',    '1.2', 'active', '2025-01-01', '2026-12-31', 'Net 14 days',                       'DDP, standard shipping 4.95 EUR',  'EUR',  30.00,  150.00, 'Priority delivery available'),
( 4, 'FC-2025-004', 4, 'Framework Agreement Sanitary Products 2025',           '1.0', 'active', '2025-03-01', '2027-02-28', 'Net 30 days',                       'DDP, free delivery above 200 EUR', 'EUR',  50.00,  200.00, 'Includes installation support hotline'),
( 5, 'FC-2025-005', 5, 'Framework Agreement Tools and Hardware 2025',          '1.1', 'active', '2025-01-01', '2026-12-31', 'Net 30 days',                       'DDP, standard shipping 5.95 EUR',  'EUR',  40.00,  200.00, 'Extended warranty on selected tools'),
( 6, 'FC-2025-006', 6, 'Framework Agreement General Supplies 2025',            '1.0', 'active', '2025-06-01', '2027-05-31', 'Net 45 days',                       'DDP, free delivery above 150 EUR', 'EUR',  30.00,  150.00, 'Multi-category cross-supply agreement'),
( 7, 'FC-2025-007', 7, 'Framework Agreement HVAC and Electrical 2025',         '1.0', 'active', '2025-01-01', '2026-12-31', 'Net 30 days',                       'DDP, express 24h available',       'EUR',  75.00,  300.00, 'Combined HVAC + Electrical'),
( 8, 'FC-2025-008', 8, 'Framework Agreement Construction Supplies 2025',       '2.0', 'active', '2025-02-01', '2027-01-31', 'Net 30 days, 2% early payment discount 10 days',    'FCA warehouse Hanover',            'EUR',  50.00,  200.00, 'Volume rebate at year end'),
( 9, 'FC-2025-009', 9, 'Framework Agreement Premium Office 2025',              '1.0', 'active', '2025-01-01', '2026-12-31', 'Net 30 days',                       'DDP next business day delivery',   'EUR',  20.00,   75.00, 'Next-day delivery guaranteed'),
(10, 'FC-2025-010',10, 'Framework Agreement Climate Technology 2025',           '1.0', 'active', '2025-05-01', '2027-04-30', 'Net 30 days',                       'DDP, project delivery negotiable', 'EUR', 100.00,  500.00, 'Includes technical planning support'),
(11, 'FC-2025-011',11, 'Framework Agreement Sanitary Express 2025',             '1.0', 'active', '2025-01-01', '2026-12-31', 'Net 14 days',                       'DDP express 24-48h',               'EUR',  30.00,  100.00, 'Express delivery specialist'),
(12, 'FC-2025-012',12, 'Framework Agreement Electrical and HVAC 2025',             '1.3', 'active', '2025-03-01', '2027-02-28', 'Net 30 days',                       'DDP, project delivery available',  'EUR',  50.00,  250.00, 'Combined technical supply'),
(13, 'FC-2025-013',13, 'Framework Agreement Professional Tools 2025',           '1.0', 'active', '2025-01-01', '2026-12-31', 'Net 30 days',                       'DDP, free delivery above 150 EUR', 'EUR',  35.00,  150.00, 'Calibration service available'),
(14, 'FC-2025-014',14, 'Framework Agreement Universal Supply 2025',             '1.0', 'active', '2025-04-01', '2027-03-31', 'Net 45 days, 3% early payment discount 14 days',    'DDP, consolidated weekly delivery', 'EUR',  25.00,  100.00, 'Consolidated delivery discounts'),
(15, 'FC-2025-015',15, 'Framework Agreement Facility Management 2025',          '2.0', 'active', '2025-01-01', '2027-12-31', 'Net 30 days',                       'DDP, free delivery above 100 EUR', 'EUR',  20.00,  100.00, '3-year strategic partnership');

SELECT setval('contracts_contract_id_seq', 15);

-- ============================================================================
-- 7. CONTRACT ARTICLES (linking contracts to articles with pricing)
-- ============================================================================
-- Prices are BELOW market/RRP to simulate framework contract advantages.
-- list_price = approximate market/retail price for comparison.
-- contract_price = negotiated framework contract price.
-- Some articles appear in multiple contracts (supplier overlap).
-- ============================================================================

INSERT INTO contract_articles (contract_article_id, contract_id, article_id, contract_price, list_price, discount_pct, min_order_qty, delivery_days, is_preferred, notes) VALUES
-- ---------------------------------------------------------------------------
-- Contract 1: Office Direct GmbH - Office Supplies
-- ---------------------------------------------------------------------------
(  1,  1,  1,   1.89,   2.49, 24.10,  1,  2, TRUE,  'Core assortment'),
(  2,  1,  2,   2.79,   3.99, 30.08,  1,  2, TRUE,  'Core assortment'),
(  3,  1,  3,   2.49,   3.79, 34.30,  1,  3, FALSE, NULL),
(  4,  1,  4,   1.19,   1.69, 29.59,  1,  2, TRUE,  'Core assortment'),
(  5,  1,  5,   1.49,   2.19, 31.96,  1,  2, FALSE, NULL),
(  6,  1,  6,   1.79,   2.49, 28.11,  1,  2, TRUE,  NULL),
(  7,  1,  7,   4.59,   6.49, 29.28,  1,  3, FALSE, NULL),
(  8,  1,  8,   1.69,   2.29, 26.20,  1,  2, FALSE, NULL),
(  9,  1,  9,   0.29,   0.45, 35.56,  10, 2, TRUE,  'Minimum 10 pieces'),
( 10,  1, 10,   0.59,   0.89, 33.71,  5,  2, FALSE, 'Minimum 5 pieces'),

-- ---------------------------------------------------------------------------
-- Contract 2: HausTechnik Mueller AG - HVAC
-- ---------------------------------------------------------------------------
( 11,  2, 11,  28.90,  38.50, 24.94,  1,  3, TRUE,  'Danfoss authorized dealer'),
( 12,  2, 12,   9.45,  13.90, 32.01,  1,  3, TRUE,  'Danfoss authorized dealer'),
( 13,  2, 13,  24.50,  33.90, 27.73,  1,  3, FALSE, NULL),
( 14,  2, 14,   8.90,  12.50, 28.80,  1,  3, FALSE, NULL),
( 15,  2, 15,  38.90,  54.90, 29.14,  1,  5, FALSE, NULL),
( 16,  2, 16, 289.00, 419.00, 31.03,  1,  5, TRUE,  'Grundfos premium partner'),
( 17,  2, 17,   4.85,   7.20, 32.64,  5,  3, FALSE, 'Minimum 5 pieces'),
( 18,  2, 18,  42.50,  59.90, 29.05,  1,  5, FALSE, NULL),
( 19,  2, 19, 195.00, 279.00, 30.11,  1,  7, FALSE, 'Made to order'),
( 20,  2, 20, 385.00, 549.00, 29.87,  1, 10, FALSE, 'Custom lengths available'),

-- ---------------------------------------------------------------------------
-- Contract 3: Elektro Schmitt GmbH - Electrical
-- ---------------------------------------------------------------------------
( 21,  3, 21,   3.29,   4.89, 32.72,  1,  2, TRUE,  'Best seller'),
( 22,  3, 22,   4.19,   5.99, 30.05,  1,  2, TRUE,  'Best seller'),
( 23,  3, 23,   4.49,   6.90, 34.93,  1,  2, TRUE,  NULL),
( 24,  3, 24,  12.90,  18.99, 32.07,  1,  3, FALSE, NULL),
( 25,  3, 25,   2.89,   4.29, 32.63,  1,  2, TRUE,  'High volume'),
( 26,  3, 26,   4.49,   6.99, 35.76,  1,  2, FALSE, NULL),
( 27,  3, 27,   0.49,   0.79, 37.97,  10, 2, TRUE,  'Per piece, minimum 10'),
( 28,  3, 28,  29.90,  44.90, 33.41,  1,  5, FALSE, NULL),
( 29,  3, 29,   7.49,  10.99, 31.85,  1,  2, FALSE, NULL),
( 30,  3, 30,  12.90,  19.90, 35.18,  1,  3, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 4: SanProfi GmbH - Sanitary
-- ---------------------------------------------------------------------------
( 31,  4, 31,  79.90, 119.00, 32.86,  1,  5, TRUE,  'Grohe Silver dealer'),
( 32,  4, 32,  52.90,  79.90, 33.79,  1,  5, TRUE,  'Grohe Silver dealer'),
( 33,  4, 33,  32.90,  49.90, 34.07,  1,  5, FALSE, NULL),
( 34,  4, 34,  69.90, 105.00, 33.43,  1,  5, TRUE,  'Hansgrohe authorized'),
( 35,  4, 35,  44.90,  69.90, 35.76,  1,  5, FALSE, NULL),
( 36,  4, 36,  59.90,  89.00, 32.70,  1,  7, TRUE,  'Geberit Gold partner'),
( 37,  4, 37,  12.90,  19.90, 35.18,  1,  3, FALSE, NULL),
( 38,  4, 38,  14.90,  22.90, 34.93,  1,  3, FALSE, NULL),
( 39,  4, 39,  16.90,  24.90, 32.13,  1,  3, FALSE, NULL),
( 40,  4, 40,  22.90,  34.90, 34.38,  1,  5, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 5: Werkzeug Wagner KG - Tools
-- ---------------------------------------------------------------------------
( 41,  5, 41,  24.90,  36.90, 32.52,  1,  3, TRUE,  'Knipex authorized'),
( 42,  5, 42,  17.90,  26.90, 33.46,  1,  3, TRUE,  'Knipex authorized'),
( 43,  5, 43,  29.90,  44.90, 33.41,  1,  3, TRUE,  'Wera premium partner'),
( 44,  5, 44,  19.90,  29.90, 33.44,  1,  3, FALSE, NULL),
( 45,  5, 45,  14.90,  22.90, 34.93,  1,  2, TRUE,  NULL),
( 46,  5, 46,  16.90,  24.99, 32.37,  1,  3, FALSE, NULL),
( 47,  5, 47,  11.90,  17.90, 33.52,  1,  2, TRUE,  'High volume item'),
( 48,  5, 48,   7.90,  11.90, 33.61,  1,  2, FALSE, NULL),
( 49,  5, 49,  16.90,  24.90, 32.13,  1,  3, FALSE, NULL),
( 50,  5, 50,  42.90,  64.90, 33.90,  1,  5, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 6: AllRound Supply GmbH - General (Office + Tools + Electrical)
-- ---------------------------------------------------------------------------
( 51,  6,  1,   2.09,   2.49, 16.06,  1,  3, FALSE, NULL),
( 52,  6,  2,   3.19,   3.99, 20.05,  1,  3, FALSE, NULL),
( 53,  6,  4,   1.29,   1.69, 23.67,  1,  3, FALSE, NULL),
( 54,  6,  6,   1.99,   2.49, 20.08,  1,  3, FALSE, NULL),
( 55,  6,  9,   0.35,   0.45, 22.22,  5,  3, FALSE, 'Minimum 5 pieces'),
( 56,  6, 25,   3.19,   4.29, 25.64,  1,  3, FALSE, NULL),
( 57,  6, 27,   0.55,   0.79, 30.38,  10, 3, FALSE, 'Per piece, minimum 10'),
( 58,  6, 41,  27.90,  36.90, 24.39,  1,  5, FALSE, NULL),
( 59,  6, 45,  16.90,  22.90, 26.20,  1,  3, FALSE, NULL),
( 60,  6, 47,  13.90,  17.90, 22.35,  1,  3, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 7: TechnoTherm GmbH - HVAC + Electrical
-- ---------------------------------------------------------------------------
( 61,  7, 11,  29.90,  38.50, 22.34,  1,  4, FALSE, NULL),
( 62,  7, 12,  10.50,  13.90, 24.46,  1,  4, FALSE, NULL),
( 63,  7, 15,  39.90,  54.90, 27.32,  1,  5, FALSE, NULL),
( 64,  7, 16, 299.00, 419.00, 28.64,  1,  7, FALSE, NULL),
( 65,  7, 21,   3.69,   4.89, 24.54,  1,  3, FALSE, NULL),
( 66,  7, 23,   4.99,   6.90, 27.68,  1,  3, FALSE, NULL),
( 67,  7, 25,   3.09,   4.29, 27.97,  1,  3, FALSE, NULL),
( 68,  7, 27,   0.52,   0.79, 34.18,  10, 3, FALSE, 'Per piece, minimum 10'),
( 69,  7, 19, 205.00, 279.00, 26.52,  1,  7, FALSE, NULL),
( 70,  7, 24,  13.90,  18.99, 26.80,  1,  3, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 8: BauBedarf Braun AG - Construction (Sanitary + Tools + Electrical)
-- ---------------------------------------------------------------------------
( 71,  8, 31,  84.90, 119.00, 28.66,  1,  5, FALSE, NULL),
( 72,  8, 36,  64.90,  89.00, 27.08,  1,  7, FALSE, NULL),
( 73,  8, 37,  13.90,  19.90, 30.15,  1,  3, FALSE, NULL),
( 74,  8, 41,  26.90,  36.90, 27.10,  1,  4, FALSE, NULL),
( 75,  8, 43,  32.90,  44.90, 26.73,  1,  4, FALSE, NULL),
( 76,  8, 47,  12.50,  17.90, 30.17,  1,  3, FALSE, NULL),
( 77,  8, 48,   8.50,  11.90, 28.57,  1,  3, FALSE, NULL),
( 78,  8, 23,   4.89,   6.90, 29.13,  1,  3, FALSE, NULL),
( 79,  8, 24,  13.50,  18.99, 28.91,  1,  3, FALSE, NULL),
( 80,  8, 49,  17.90,  24.90, 28.11,  1,  4, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 9: ProOffice Solutions GmbH - Premium Office
-- ---------------------------------------------------------------------------
( 81,  9,  1,   1.79,   2.49, 28.11,  1,  1, TRUE,  'Next day delivery'),
( 82,  9,  2,   2.59,   3.99, 35.09,  1,  1, TRUE,  'Next day delivery'),
( 83,  9,  3,   2.29,   3.79, 39.58,  1,  1, TRUE,  'Next day delivery'),
( 84,  9,  4,   1.09,   1.69, 35.50,  1,  1, TRUE,  'Next day delivery'),
( 85,  9,  5,   1.39,   2.19, 36.53,  1,  1, TRUE,  'Next day delivery'),
( 86,  9,  6,   1.69,   2.49, 32.13,  1,  1, TRUE,  'Next day delivery'),
( 87,  9,  7,   4.29,   6.49, 33.90,  1,  1, FALSE, NULL),
( 88,  9,  8,   1.59,   2.29, 30.57,  1,  1, FALSE, NULL),
( 89,  9,  9,   0.25,   0.45, 44.44,  10, 1, TRUE,  'Best price, min 10'),
( 90,  9, 10,   0.49,   0.89, 44.94,  5,  1, TRUE,  'Best price, min 5'),

-- ---------------------------------------------------------------------------
-- Contract 10: KlimaTech GmbH - Climate Technology
-- ---------------------------------------------------------------------------
( 91, 10, 11,  27.50,  38.50, 28.57,  1,  5, TRUE,  'Danfoss certified'),
( 92, 10, 12,   8.90,  13.90, 35.97,  1,  5, TRUE,  'Danfoss certified'),
( 93, 10, 13,  23.90,  33.90, 29.50,  1,  5, TRUE,  'Oventrop certified'),
( 94, 10, 14,   8.50,  12.50, 32.00,  1,  5, TRUE,  'Oventrop certified'),
( 95, 10, 16, 279.00, 419.00, 33.41,  1,  7, TRUE,  'Best price Grundfos'),
( 96, 10, 18,  39.90,  59.90, 33.39,  1,  5, FALSE, NULL),
( 97, 10, 19, 189.00, 279.00, 32.26,  1, 10, FALSE, NULL),
( 98, 10, 20, 369.00, 549.00, 32.79,  1, 14, FALSE, 'Custom lengths on request'),

-- ---------------------------------------------------------------------------
-- Contract 11: Sanitaer Express GmbH - Sanitary Express
-- ---------------------------------------------------------------------------
( 99, 11, 31,  85.90, 119.00, 27.81,  1,  2, FALSE, 'Express 24h'),
(100, 11, 32,  56.90,  79.90, 28.79,  1,  2, FALSE, 'Express 24h'),
(101, 11, 34,  74.90, 105.00, 28.67,  1,  2, FALSE, 'Express 24h'),
(102, 11, 35,  47.90,  69.90, 31.47,  1,  2, FALSE, 'Express 24h'),
(103, 11, 36,  62.90,  89.00, 29.33,  1,  3, FALSE, NULL),
(104, 11, 37,  13.50,  19.90, 32.16,  1,  2, FALSE, NULL),
(105, 11, 38,  15.90,  22.90, 30.57,  1,  2, FALSE, NULL),
(106, 11, 39,  17.90,  24.90, 28.11,  1,  2, FALSE, NULL),
(107, 11, 40,  24.90,  34.90, 28.65,  1,  2, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 12: ElektroProfi AG - Electrical + HVAC
-- ---------------------------------------------------------------------------
(108, 12, 21,   3.49,   4.89, 28.63,  1,  3, FALSE, NULL),
(109, 12, 22,   4.39,   5.99, 26.71,  1,  3, FALSE, NULL),
(110, 12, 23,   4.69,   6.90, 32.03,  1,  3, FALSE, NULL),
(111, 12, 25,   2.99,   4.29, 30.30,  1,  3, FALSE, NULL),
(112, 12, 26,   4.69,   6.99, 32.90,  1,  3, FALSE, NULL),
(113, 12, 27,   0.45,   0.79, 43.04,  20, 3, TRUE,  'Best price, min 20'),
(114, 12, 11,  30.90,  38.50, 19.74,  1,  5, FALSE, NULL),
(115, 12, 12,  10.90,  13.90, 21.58,  1,  5, FALSE, NULL),
(116, 12, 15,  41.90,  54.90, 23.68,  1,  7, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 13: MegaTool GmbH - Professional Tools
-- ---------------------------------------------------------------------------
(117, 13, 41,  23.90,  36.90, 35.23,  1,  2, TRUE,  'Best price Knipex'),
(118, 13, 42,  16.90,  26.90, 37.17,  1,  2, TRUE,  'Best price Knipex'),
(119, 13, 43,  28.90,  44.90, 35.63,  1,  2, TRUE,  'Best price Wera'),
(120, 13, 44,  18.90,  29.90, 36.79,  1,  2, TRUE,  'Best price Wera'),
(121, 13, 45,  13.90,  22.90, 39.30,  1,  2, TRUE,  NULL),
(122, 13, 46,  15.90,  24.99, 36.37,  1,  2, FALSE, NULL),
(123, 13, 47,  10.90,  17.90, 39.11,  1,  2, TRUE,  NULL),
(124, 13, 48,   6.90,  11.90, 42.02,  1,  2, TRUE,  NULL),
(125, 13, 49,  15.90,  24.90, 36.14,  1,  2, FALSE, NULL),
(126, 13, 50,  39.90,  64.90, 38.52,  1,  3, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 14: Universal Supplies AG - Cross-category
-- ---------------------------------------------------------------------------
(127, 14,  1,   2.19,   2.49, 12.05,  1,  4, FALSE, NULL),
(128, 14,  2,   3.39,   3.99, 15.04,  1,  4, FALSE, NULL),
(129, 14, 25,   3.39,   4.29, 20.98,  1,  4, FALSE, NULL),
(130, 14, 31,  89.90, 119.00, 24.45,  1,  7, FALSE, NULL),
(131, 14, 41,  28.90,  36.90, 21.68,  1,  5, FALSE, NULL),
(132, 14, 43,  34.90,  44.90, 22.27,  1,  5, FALSE, NULL),
(133, 14, 47,  14.50,  17.90, 18.99,  1,  4, FALSE, NULL),
(134, 14, 11,  32.90,  38.50, 14.55,  1,  5, FALSE, NULL),
(135, 14, 23,   5.49,   6.90, 20.43,  1,  4, FALSE, NULL),

-- ---------------------------------------------------------------------------
-- Contract 15: FacilityPro GmbH - Facility Management (all categories)
-- ---------------------------------------------------------------------------
(136, 15,  1,   1.99,   2.49, 20.08,  1,  3, FALSE, NULL),
(137, 15,  2,   2.89,   3.99, 27.57,  1,  3, FALSE, NULL),
(138, 15,  4,   1.15,   1.69, 31.95,  1,  3, FALSE, NULL),
(139, 15,  6,   1.85,   2.49, 25.70,  1,  3, FALSE, NULL),
(140, 15, 11,  29.50,  38.50, 23.38,  1,  5, FALSE, NULL),
(141, 15, 12,   9.90,  13.90, 28.78,  1,  5, FALSE, NULL),
(142, 15, 21,   3.59,   4.89, 26.58,  1,  3, FALSE, NULL),
(143, 15, 25,   3.09,   4.29, 27.97,  1,  3, FALSE, NULL),
(144, 15, 31,  82.90, 119.00, 30.34,  1,  5, FALSE, NULL),
(145, 15, 34,  72.90, 105.00, 30.57,  1,  5, FALSE, NULL),
(146, 15, 36,  61.90,  89.00, 30.45,  1,  7, FALSE, NULL),
(147, 15, 41,  25.90,  36.90, 29.81,  1,  4, FALSE, NULL),
(148, 15, 43,  31.90,  44.90, 28.95,  1,  4, FALSE, NULL),
(149, 15, 47,  12.90,  17.90, 27.93,  1,  3, FALSE, NULL),
(150, 15, 50,  44.90,  64.90, 30.82,  1,  5, FALSE, NULL);

SELECT setval('contract_articles_contract_article_id_seq', 150);

-- ============================================================================
-- 8. TIERED PRICING (Volume Discounts)
-- ============================================================================
-- Tiered pricing provides additional savings at higher quantities.
-- This is critical for the demo: comparing single-unit pricing vs. bulk.
-- ============================================================================

INSERT INTO tiered_pricing (contract_article_id, min_quantity, max_quantity, tier_price, discount_pct) VALUES
-- tesa Film - Office Direct (contract_article_id = 1)
( 1,   1,   9,  1.89,  0.00),
( 1,  10,  49,  1.69,  10.58),
( 1,  50, 199,  1.49,  21.16),
( 1, 200, NULL, 1.29,  31.75),

-- Post-it Notes - Office Direct (contract_article_id = 2)
( 2,   1,   9,  2.79,  0.00),
( 2,  10,  49,  2.49,  10.75),
( 2,  50, 199,  2.19,  21.51),
( 2, 200, NULL, 1.89,  32.26),

-- Leitz Folder - Office Direct (contract_article_id = 3)
( 3,   1,  19,  2.49,  0.00),
( 3,  20,  49,  2.19,  12.05),
( 3,  50, 199,  1.89,  24.10),
( 3, 200, NULL, 1.59,  36.14),

-- STABILO BOSS - Office Direct (contract_article_id = 4)
( 4,   1,  19,  1.19,  0.00),
( 4,  20,  99,  0.99,  16.81),
( 4, 100, NULL, 0.85,  28.57),

-- Pritt Glue Stick - Office Direct (contract_article_id = 6)
( 6,   1,  19,  1.79,  0.00),
( 6,  20,  99,  1.59,  11.17),
( 6, 100, NULL, 1.39,  22.35),

-- BIC Cristal - Office Direct (contract_article_id = 9)
( 9,  10,  49,  0.29,  0.00),
( 9,  50, 199,  0.22,  24.14),
( 9, 200, 499,  0.18,  37.93),
( 9, 500, NULL, 0.15,  48.28),

-- Danfoss RA-N 15 - HausTechnik Mueller (contract_article_id = 11)
(11,   1,   9, 28.90,  0.00),
(11,  10,  24, 26.90,  6.92),
(11,  25,  49, 24.90, 13.84),
(11,  50, NULL, 22.90, 20.76),

-- Danfoss RAE-K Sensor - HausTechnik Mueller (contract_article_id = 12)
(12,   1,   9,  9.45,  0.00),
(12,  10,  24,  8.50, 10.05),
(12,  25,  49,  7.50, 20.63),
(12,  50, NULL,  6.90, 26.98),

-- Grundfos Alpha2 Pump - HausTechnik Mueller (contract_article_id = 16)
(16,   1,   4, 289.00,  0.00),
(16,   5,   9, 275.00,  4.84),
(16,  10,  19, 259.00, 10.38),
(16,  20, NULL, 245.00, 15.22),

-- Busch-Jaeger Socket - Elektro Schmitt (contract_article_id = 21)
(21,   1,  19,  3.29,  0.00),
(21,  20,  49,  2.99,  9.12),
(21,  50,  99,  2.69, 18.24),
(21, 100, NULL,  2.39, 27.36),

-- Busch-Jaeger Switch - Elektro Schmitt (contract_article_id = 22)
(22,   1,  19,  4.19,  0.00),
(22,  20,  49,  3.79,  9.55),
(22,  50,  99,  3.49, 16.71),
(22, 100, NULL,  3.19, 23.87),

-- Hager MCB B16 - Elektro Schmitt (contract_article_id = 23)
(23,   1,   9,  4.49,  0.00),
(23,  10,  24,  3.99, 11.14),
(23,  25,  49,  3.49, 22.27),
(23,  50, NULL,  2.99, 33.41),

-- OSRAM LED - Elektro Schmitt (contract_article_id = 25)
(25,   1,   9,  2.89,  0.00),
(25,  10,  49,  2.49, 13.84),
(25,  50,  99,  2.19, 24.22),
(25, 100, NULL,  1.89, 34.60),

-- WAGO Connector - Elektro Schmitt (contract_article_id = 27)
(27,  10,  49,  0.49,  0.00),
(27,  50,  99,  0.42, 14.29),
(27, 100, 499,  0.35, 28.57),
(27, 500, NULL,  0.29, 40.82),

-- Grohe Eurosmart - SanProfi (contract_article_id = 31)
(31,   1,   4, 79.90,  0.00),
(31,   5,   9, 74.90,  6.26),
(31,  10,  24, 69.90, 12.52),
(31,  25, NULL, 64.90, 18.77),

-- Grohe BauEdge - SanProfi (contract_article_id = 32)
(32,   1,   4, 52.90,  0.00),
(32,   5,   9, 49.90,  5.67),
(32,  10,  24, 46.90, 11.34),
(32,  25, NULL, 42.90, 18.90),

-- Hansgrohe Logis - SanProfi (contract_article_id = 34)
(34,   1,   4, 69.90,  0.00),
(34,   5,   9, 64.90,  7.15),
(34,  10,  24, 59.90, 14.31),
(34,  25, NULL, 54.90, 21.46),

-- Geberit Sigma20 - SanProfi (contract_article_id = 36)
(36,   1,   4, 59.90,  0.00),
(36,   5,   9, 55.90,  6.68),
(36,  10,  24, 52.90, 11.69),
(36,  25, NULL, 49.90, 16.69),

-- Knipex Cobra - Werkzeug Wagner (contract_article_id = 41)
(41,   1,   4, 24.90,  0.00),
(41,   5,   9, 22.90,  8.03),
(41,  10,  24, 20.90, 16.06),
(41,  25, NULL, 18.90, 24.10),

-- Wera Kraftform Set - Werkzeug Wagner (contract_article_id = 43)
(43,   1,   4, 29.90,  0.00),
(43,   5,   9, 27.90,  6.69),
(43,  10,  24, 25.90, 13.38),
(43,  25, NULL, 23.90, 20.07),

-- Fischer DuoPower 8x40 - Werkzeug Wagner (contract_article_id = 47)
(47,   1,   4, 11.90,  0.00),
(47,   5,   9, 10.90,  8.40),
(47,  10,  24,  9.90, 16.81),
(47,  25, NULL,  8.90, 25.21),

-- Fischer DuoPower 6x30 - Werkzeug Wagner (contract_article_id = 48)
(48,   1,   4,  7.90,  0.00),
(48,   5,   9,  6.90, 12.66),
(48,  10,  24,  5.90, 25.32),
(48,  25, NULL,  4.90, 37.97),

-- Bosch Drill Bit Set - Werkzeug Wagner (contract_article_id = 49)
(49,   1,   4, 16.90,  0.00),
(49,   5,   9, 15.50,  8.28),
(49,  10, NULL, 13.90, 17.75),

-- Knipex Cobra - MegaTool BEST PRICE (contract_article_id = 117)
(117,   1,   4, 23.90,  0.00),
(117,   5,   9, 21.90,  8.37),
(117,  10,  24, 19.90, 16.74),
(117,  25, NULL, 17.90, 25.10),

-- Fischer DuoPower 8x40 - MegaTool BEST PRICE (contract_article_id = 123)
(123,   1,   4, 10.90,  0.00),
(123,   5,   9,  9.90,  9.17),
(123,  10,  24,  8.90, 18.35),
(123,  25,  49,  7.90, 27.52),
(123,  50, NULL,  6.90, 36.70),

-- Grundfos Alpha2 - KlimaTech BEST PRICE (contract_article_id = 95)
(95,   1,   4, 279.00,  0.00),
(95,   5,   9, 265.00,  5.02),
(95,  10,  19, 249.00, 10.75),
(95,  20, NULL, 235.00, 15.77),

-- Danfoss RA-N 15 - KlimaTech BEST PRICE (contract_article_id = 91)
(91,   1,   9, 27.50,  0.00),
(91,  10,  24, 25.50,  7.27),
(91,  25,  49, 23.50, 14.55),
(91,  50, NULL, 21.50, 21.82),

-- Grohe Eurosmart - FacilityPro (contract_article_id = 144)
(144,   1,   4, 82.90,  0.00),
(144,   5,   9, 78.90,  4.83),
(144,  10,  24, 74.90,  9.65),
(144,  25, NULL, 69.90, 15.68),

-- BIC Cristal - ProOffice BEST PRICE (contract_article_id = 89)
(89,  10,  49,  0.25,  0.00),
(89,  50, 199,  0.19, 24.00),
(89, 200, 499,  0.15, 40.00),
(89, 500, NULL,  0.12, 52.00),

-- Post-it Notes - ProOffice (contract_article_id = 82)
(82,   1,   9,  2.59,  0.00),
(82,  10,  49,  2.29, 11.58),
(82,  50, 199,  1.99, 23.17),
(82, 200, NULL, 1.69, 34.75);

-- ============================================================================
-- 9. VIEWS FOR EASY QUERYING
-- ============================================================================

-- Best price per article across all active contracts
CREATE OR REPLACE VIEW v_best_contract_price AS
SELECT
    a.article_id,
    a.ean,
    a.article_name,
    a.brand,
    cat.category_name,
    s.supplier_name,
    c.contract_number,
    ca.contract_price,
    ca.list_price,
    ca.discount_pct,
    ca.delivery_days,
    ca.min_order_qty,
    c.valid_from,
    c.valid_until,
    ROW_NUMBER() OVER (
        PARTITION BY a.article_id
        ORDER BY ca.contract_price ASC
    ) AS price_rank
FROM articles a
JOIN categories cat ON cat.category_id = a.category_id
JOIN contract_articles ca ON ca.article_id = a.article_id
JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
JOIN suppliers s ON s.supplier_id = c.supplier_id;

-- Best tiered price per article for a given quantity
CREATE OR REPLACE VIEW v_best_tiered_price AS
SELECT
    a.article_id,
    a.ean,
    a.article_name,
    a.brand,
    cat.category_name,
    s.supplier_name,
    c.contract_number,
    ca.contract_price AS base_price,
    ca.list_price,
    tp.min_quantity,
    tp.max_quantity,
    tp.tier_price,
    tp.discount_pct AS tier_discount_pct,
    ca.delivery_days,
    c.valid_from,
    c.valid_until
FROM articles a
JOIN categories cat ON cat.category_id = a.category_id
JOIN contract_articles ca ON ca.article_id = a.article_id
JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
JOIN suppliers s ON s.supplier_id = c.supplier_id
JOIN tiered_pricing tp ON tp.contract_article_id = ca.contract_article_id;

-- Contract overview with article count
CREATE OR REPLACE VIEW v_contract_overview AS
SELECT
    c.contract_id,
    c.contract_number,
    c.title,
    c.version,
    c.status,
    s.supplier_name,
    s.contact_person,
    s.email,
    s.phone,
    c.valid_from,
    c.valid_until,
    c.payment_terms,
    c.delivery_terms,
    c.minimum_order_value,
    c.free_shipping_threshold,
    COUNT(ca.contract_article_id) AS article_count,
    ROUND(AVG(ca.discount_pct), 2) AS avg_discount_pct
FROM contracts c
JOIN suppliers s ON s.supplier_id = c.supplier_id
LEFT JOIN contract_articles ca ON ca.contract_id = c.contract_id
GROUP BY c.contract_id, c.contract_number, c.title, c.version, c.status,
         s.supplier_name, s.contact_person, s.email, s.phone,
         c.valid_from, c.valid_until, c.payment_terms, c.delivery_terms,
         c.minimum_order_value, c.free_shipping_threshold;

-- Article search view (for fuzzy matching)
CREATE OR REPLACE VIEW v_article_search AS
SELECT
    a.article_id,
    a.ean,
    a.article_name,
    a.brand,
    a.manufacturer,
    a.description,
    cat.category_name,
    a.unit,
    COUNT(DISTINCT ca.contract_id) AS contract_count,
    MIN(ca.contract_price) AS best_contract_price,
    MAX(ca.list_price) AS list_price,
    ROUND(((MAX(ca.list_price) - MIN(ca.contract_price)) / NULLIF(MAX(ca.list_price), 0)) * 100, 2) AS max_saving_pct
FROM articles a
JOIN categories cat ON cat.category_id = a.category_id
LEFT JOIN contract_articles ca ON ca.article_id = a.article_id
LEFT JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
GROUP BY a.article_id, a.ean, a.article_name, a.brand, a.manufacturer,
         a.description, cat.category_name, a.unit;

-- Supplier article matrix
CREATE OR REPLACE VIEW v_supplier_article_matrix AS
SELECT
    s.supplier_name,
    a.ean,
    a.article_name,
    a.brand,
    cat.category_name,
    ca.contract_price,
    ca.list_price,
    ca.discount_pct,
    ca.delivery_days,
    ca.is_preferred,
    c.contract_number,
    c.valid_until
FROM suppliers s
JOIN contracts c ON c.supplier_id = s.supplier_id AND c.status = 'active'
JOIN contract_articles ca ON ca.contract_id = c.contract_id
JOIN articles a ON a.article_id = ca.article_id
JOIN categories cat ON cat.category_id = a.category_id
ORDER BY s.supplier_name, cat.category_name, a.article_name;

-- ============================================================================
-- 10. UTILITY FUNCTIONS
-- ============================================================================

-- Function: Get best price for an article at a specific quantity
CREATE OR REPLACE FUNCTION get_best_price(
    p_article_id INT,
    p_quantity INT DEFAULT 1
)
RETURNS TABLE (
    supplier_name     VARCHAR,
    contract_number   VARCHAR,
    unit_price        NUMERIC,
    total_price       NUMERIC,
    savings_vs_list   NUMERIC,
    delivery_days     INT,
    price_type        VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    -- Check tiered pricing first
    SELECT
        s.supplier_name,
        c.contract_number,
        tp.tier_price AS unit_price,
        (tp.tier_price * p_quantity)::NUMERIC AS total_price,
        ((ca.list_price - tp.tier_price) * p_quantity)::NUMERIC AS savings_vs_list,
        ca.delivery_days,
        'tiered'::VARCHAR AS price_type
    FROM contract_articles ca
    JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
        AND CURRENT_DATE BETWEEN c.valid_from AND c.valid_until
    JOIN suppliers s ON s.supplier_id = c.supplier_id
    JOIN tiered_pricing tp ON tp.contract_article_id = ca.contract_article_id
    WHERE ca.article_id = p_article_id
      AND p_quantity >= tp.min_quantity
      AND (tp.max_quantity IS NULL OR p_quantity <= tp.max_quantity)
      AND p_quantity >= ca.min_order_qty

    UNION ALL

    -- Also include base contract prices (when no tier matches)
    SELECT
        s.supplier_name,
        c.contract_number,
        ca.contract_price AS unit_price,
        (ca.contract_price * p_quantity)::NUMERIC AS total_price,
        ((ca.list_price - ca.contract_price) * p_quantity)::NUMERIC AS savings_vs_list,
        ca.delivery_days,
        'contract'::VARCHAR AS price_type
    FROM contract_articles ca
    JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
        AND CURRENT_DATE BETWEEN c.valid_from AND c.valid_until
    JOIN suppliers s ON s.supplier_id = c.supplier_id
    WHERE ca.article_id = p_article_id
      AND p_quantity >= ca.min_order_qty
      AND NOT EXISTS (
          SELECT 1 FROM tiered_pricing tp2
          WHERE tp2.contract_article_id = ca.contract_article_id
            AND p_quantity >= tp2.min_quantity
            AND (tp2.max_quantity IS NULL OR p_quantity <= tp2.max_quantity)
      )

    ORDER BY unit_price ASC;
END;
$$ LANGUAGE plpgsql;

-- Function: Search articles by name or EAN (fuzzy)
CREATE OR REPLACE FUNCTION search_articles(
    p_search_term VARCHAR
)
RETURNS TABLE (
    article_id      INT,
    ean             VARCHAR,
    article_name    VARCHAR,
    brand           VARCHAR,
    category_name   VARCHAR,
    best_price      NUMERIC,
    list_price      NUMERIC,
    contract_count  BIGINT,
    relevance       REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        a.article_id,
        a.ean::VARCHAR,
        a.article_name::VARCHAR,
        a.brand::VARCHAR,
        cat.category_name::VARCHAR,
        MIN(ca.contract_price) AS best_price,
        MAX(ca.list_price) AS list_price,
        COUNT(DISTINCT ca.contract_id) AS contract_count,
        CASE
            WHEN a.ean = p_search_term THEN 1.0
            WHEN LOWER(a.article_name) = LOWER(p_search_term) THEN 0.95
            WHEN LOWER(a.article_name) LIKE LOWER(p_search_term) || '%' THEN 0.85
            WHEN LOWER(a.article_name) LIKE '%' || LOWER(p_search_term) || '%' THEN 0.7
            WHEN LOWER(a.brand) = LOWER(p_search_term) THEN 0.6
            WHEN LOWER(a.description) LIKE '%' || LOWER(p_search_term) || '%' THEN 0.5
            ELSE 0.3
        END::REAL AS relevance
    FROM articles a
    JOIN categories cat ON cat.category_id = a.category_id
    LEFT JOIN contract_articles ca ON ca.article_id = a.article_id
    LEFT JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
    WHERE a.ean = p_search_term
       OR LOWER(a.article_name) LIKE '%' || LOWER(p_search_term) || '%'
       OR LOWER(a.brand) LIKE '%' || LOWER(p_search_term) || '%'
       OR LOWER(a.description) LIKE '%' || LOWER(p_search_term) || '%'
    GROUP BY a.article_id, a.ean, a.article_name, a.brand, cat.category_name, a.description
    ORDER BY relevance DESC, best_price ASC;
END;
$$ LANGUAGE plpgsql;

-- Function: Recommend optimal order quantity (checks if ordering more is cheaper)
CREATE OR REPLACE FUNCTION recommend_order(
    p_article_id INT,
    p_desired_qty INT DEFAULT 1
)
RETURNS TABLE (
    supplier_name       VARCHAR,
    contract_number     VARCHAR,
    recommended_qty     INT,
    unit_price          NUMERIC,
    total_price         NUMERIC,
    savings_vs_list     NUMERIC,
    savings_pct         NUMERIC,
    delivery_days       INT,
    recommendation      VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    WITH price_options AS (
        -- Option 1: exact desired quantity
        SELECT
            s.supplier_name,
            c.contract_number,
            p_desired_qty AS qty,
            COALESCE(
                (SELECT tp.tier_price FROM tiered_pricing tp
                 WHERE tp.contract_article_id = ca.contract_article_id
                   AND p_desired_qty >= tp.min_quantity
                   AND (tp.max_quantity IS NULL OR p_desired_qty <= tp.max_quantity)
                 ORDER BY tp.tier_price ASC LIMIT 1),
                ca.contract_price
            ) AS u_price,
            ca.list_price,
            ca.delivery_days,
            'exact quantity'::VARCHAR AS rec
        FROM contract_articles ca
        JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
            AND CURRENT_DATE BETWEEN c.valid_from AND c.valid_until
        JOIN suppliers s ON s.supplier_id = c.supplier_id
        WHERE ca.article_id = p_article_id
          AND p_desired_qty >= ca.min_order_qty

        UNION ALL

        -- Option 2: next tier threshold (if ordering slightly more unlocks better price)
        SELECT
            s.supplier_name,
            c.contract_number,
            tp.min_quantity AS qty,
            tp.tier_price AS u_price,
            ca.list_price,
            ca.delivery_days,
            'volume tier - order more to save'::VARCHAR AS rec
        FROM contract_articles ca
        JOIN contracts c ON c.contract_id = ca.contract_id AND c.status = 'active'
            AND CURRENT_DATE BETWEEN c.valid_from AND c.valid_until
        JOIN suppliers s ON s.supplier_id = c.supplier_id
        JOIN tiered_pricing tp ON tp.contract_article_id = ca.contract_article_id
        WHERE ca.article_id = p_article_id
          AND tp.min_quantity > p_desired_qty
          AND tp.min_quantity <= p_desired_qty * 2  -- only suggest up to 2x desired qty
    )
    SELECT
        po.supplier_name,
        po.contract_number,
        po.qty AS recommended_qty,
        po.u_price AS unit_price,
        (po.u_price * po.qty)::NUMERIC AS total_price,
        ((po.list_price - po.u_price) * po.qty)::NUMERIC AS savings_vs_list,
        ROUND(((po.list_price - po.u_price) / NULLIF(po.list_price, 0)) * 100, 1) AS savings_pct,
        po.delivery_days,
        po.rec AS recommendation
    FROM price_options po
    ORDER BY po.u_price ASC, po.qty ASC;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- 11. VERIFICATION QUERIES (uncomment to test after loading)
-- ============================================================================

-- Verify data integrity
DO $$
DECLARE
    v_suppliers INT;
    v_articles INT;
    v_contracts INT;
    v_contract_articles INT;
    v_tiers INT;
    v_categories INT;
BEGIN
    SELECT COUNT(*) INTO v_categories FROM categories;
    SELECT COUNT(*) INTO v_suppliers FROM suppliers;
    SELECT COUNT(*) INTO v_articles FROM articles;
    SELECT COUNT(*) INTO v_contracts FROM contracts;
    SELECT COUNT(*) INTO v_contract_articles FROM contract_articles;
    SELECT COUNT(*) INTO v_tiers FROM tiered_pricing;

    RAISE NOTICE '=== CONTRACT MANAGEMENT DB LOADED ===';
    RAISE NOTICE 'Categories:        %', v_categories;
    RAISE NOTICE 'Suppliers:         %', v_suppliers;
    RAISE NOTICE 'Articles:          %', v_articles;
    RAISE NOTICE 'Contracts:         %', v_contracts;
    RAISE NOTICE 'Contract Articles: %', v_contract_articles;
    RAISE NOTICE 'Tiered Pricings:   %', v_tiers;
    RAISE NOTICE '=====================================';
END $$;

-- ============================================================================
-- 12. TABLE AND COLUMN COMMENTS (LLM-friendly schema documentation)
-- ============================================================================
-- These comments make the schema self-documenting for AI/LLM-based DB agents.
-- An LLM agent can query pg_catalog.pg_description to understand the schema.
-- ============================================================================

COMMENT ON SCHEMA contracts IS 'Procurement framework contract database for a facility management company. Contains supplier master data, framework contracts with negotiated pricing, articles identified by EAN-13 barcodes, and volume-based tiered pricing. Use search_articles() to find articles and get_best_price() to compare pricing across contracts.';

COMMENT ON TABLE categories IS 'Product categories: Office Supplies, HVAC, Electrical, Sanitary, Tools and Accessories';
COMMENT ON TABLE suppliers IS 'B2B supplier master data including contact details, address, website, and online shop URL';
COMMENT ON TABLE articles IS 'Product catalog with unique EAN-13 barcodes. Each article belongs to one category. Articles are real products from well-known brands.';
COMMENT ON TABLE contracts IS 'Framework agreements with suppliers. Each contract has a validity period, payment and delivery terms, and links to articles via contract_articles.';
COMMENT ON TABLE contract_articles IS 'Links articles to contracts with negotiated pricing. contract_price is always below list_price. Multiple suppliers may offer the same article at different prices.';
COMMENT ON TABLE tiered_pricing IS 'Volume-based discount tiers for contract articles. Higher quantities unlock lower unit prices. Use get_best_price(article_id, quantity) to find the optimal price for a given order quantity.';

COMMENT ON COLUMN articles.ean IS 'EAN-13 barcode number - unique product identifier, 13 digits';
COMMENT ON COLUMN articles.article_name IS 'Full product name including brand, model, key specifications';
COMMENT ON COLUMN articles.brand IS 'Product brand name (e.g., Grohe, Knipex, tesa)';
COMMENT ON COLUMN articles.manufacturer IS 'Legal manufacturer entity name';
COMMENT ON COLUMN articles.description IS 'Detailed product description with technical specifications';

COMMENT ON COLUMN contract_articles.contract_price IS 'Negotiated framework contract unit price in EUR - always lower than list_price';
COMMENT ON COLUMN contract_articles.list_price IS 'Approximate market/retail unit price in EUR for comparison';
COMMENT ON COLUMN contract_articles.discount_pct IS 'Percentage discount vs list price: ((list_price - contract_price) / list_price) * 100';
COMMENT ON COLUMN contract_articles.min_order_qty IS 'Minimum order quantity to activate this contract price';
COMMENT ON COLUMN contract_articles.delivery_days IS 'Expected delivery time in business days';
COMMENT ON COLUMN contract_articles.is_preferred IS 'TRUE if this is the recommended/preferred supplier for this article';

COMMENT ON COLUMN tiered_pricing.min_quantity IS 'Minimum order quantity to qualify for this tier price';
COMMENT ON COLUMN tiered_pricing.max_quantity IS 'Maximum quantity for this tier (NULL means unlimited/no upper bound)';
COMMENT ON COLUMN tiered_pricing.tier_price IS 'Unit price in EUR at this quantity tier - decreases with higher quantities';

COMMENT ON VIEW v_best_contract_price IS 'Ranked list of all contract prices per article. price_rank=1 is the cheapest option. Filter by article_id or ean to compare suppliers.';
COMMENT ON VIEW v_best_tiered_price IS 'All tiered pricing options across contracts. Join with a specific quantity to find applicable tiers.';
COMMENT ON VIEW v_contract_overview IS 'Summary of all contracts with supplier info, article count, and average discount percentage.';
COMMENT ON VIEW v_article_search IS 'Article catalog with best available contract price and maximum savings percentage vs list price.';
COMMENT ON VIEW v_supplier_article_matrix IS 'Complete cross-reference of which suppliers offer which articles at what prices.';

COMMENT ON FUNCTION get_best_price(INT, INT) IS 'Returns the best available price for an article at a given quantity, considering both base contract prices and volume tiered pricing. Results sorted by unit_price ascending (cheapest first).';
COMMENT ON FUNCTION search_articles(VARCHAR) IS 'Fuzzy search for articles by EAN, name, brand, or description. Returns results ranked by relevance score (1.0 = exact EAN match, 0.95 = exact name match, down to 0.3 = partial description match).';
COMMENT ON FUNCTION recommend_order(INT, INT) IS 'Smart order recommendation: compares exact-quantity pricing with nearby volume tier thresholds. Shows if ordering slightly more units unlocks a cheaper tier, potentially reducing total cost. Suggests up to 2x the desired quantity.';

COMMIT;

-- ============================================================================
-- END OF FILE
-- ============================================================================
