--
-- PostgreSQL database dump
--

-- Dumped from database version 16.3
-- Dumped by pg_dump version 16.3

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.audit_logs (
    log_id integer NOT NULL,
    user_id integer,
    action character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.audit_logs OWNER TO postgres;

--
-- Name: audit_logs_log_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.audit_logs_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.audit_logs_log_id_seq OWNER TO postgres;

--
-- Name: audit_logs_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.audit_logs_log_id_seq OWNED BY public.audit_logs.log_id;


--
-- Name: bookings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.bookings (
    booking_id integer NOT NULL,
    room_id integer,
    guest_id integer,
    check_in date NOT NULL,
    check_out date NOT NULL,
    booking_source character varying(20),
    status character varying(30) NOT NULL,
    total_amount numeric(10,2) NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    base_amount numeric(10,2),
    gst_amount numeric(10,2),
    room_type_id integer,
    convenience_fee numeric(10,2) DEFAULT 0,
    convenience_gst numeric(10,2) DEFAULT 0,
    grand_total numeric(10,2),
    cancelled_at timestamp without time zone,
    cancel_reason character varying(100)
);


ALTER TABLE public.bookings OWNER TO postgres;

--
-- Name: bookings_booking_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.bookings_booking_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.bookings_booking_id_seq OWNER TO postgres;

--
-- Name: bookings_booking_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.bookings_booking_id_seq OWNED BY public.bookings.booking_id;


--
-- Name: contact_enquiries; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.contact_enquiries (
    enquiry_id integer NOT NULL,
    name character varying(100) NOT NULL,
    email character varying(100) NOT NULL,
    phone character varying(15) NOT NULL,
    message character varying(1000) NOT NULL,
    status character varying(20),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.contact_enquiries OWNER TO postgres;

--
-- Name: contact_enquiries_enquiry_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.contact_enquiries_enquiry_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.contact_enquiries_enquiry_id_seq OWNER TO postgres;

--
-- Name: contact_enquiries_enquiry_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.contact_enquiries_enquiry_id_seq OWNED BY public.contact_enquiries.enquiry_id;


--
-- Name: guests; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.guests (
    guest_id integer NOT NULL,
    name character varying(100) NOT NULL,
    phone character varying(15) NOT NULL,
    email character varying(100),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.guests OWNER TO postgres;

--
-- Name: guests_guest_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.guests_guest_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.guests_guest_id_seq OWNER TO postgres;

--
-- Name: guests_guest_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.guests_guest_id_seq OWNED BY public.guests.guest_id;


--
-- Name: payments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.payments (
    payment_id integer NOT NULL,
    booking_id integer,
    gateway character varying(30),
    gateway_order_id character varying(100),
    gateway_payment_id character varying(100),
    amount numeric(10,2),
    status character varying(20),
    paid_at timestamp without time zone,
    order_id character varying,
    payment_id_gateway character varying,
    currency character varying DEFAULT 'INR'::character varying,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.payments OWNER TO postgres;

--
-- Name: payments_payment_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.payments_payment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.payments_payment_id_seq OWNER TO postgres;

--
-- Name: payments_payment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.payments_payment_id_seq OWNED BY public.payments.payment_id;


--
-- Name: room_type_availability; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.room_type_availability (
    id integer NOT NULL,
    room_type_id integer NOT NULL,
    date date NOT NULL,
    is_available boolean,
    reason character varying(100),
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.room_type_availability OWNER TO postgres;

--
-- Name: room_type_availability_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.room_type_availability_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.room_type_availability_id_seq OWNER TO postgres;

--
-- Name: room_type_availability_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.room_type_availability_id_seq OWNED BY public.room_type_availability.id;


--
-- Name: room_types; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.room_types (
    room_type_id integer NOT NULL,
    name character varying(50) NOT NULL,
    price_per_night numeric(10,2) NOT NULL,
    max_occupancy integer NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    is_active boolean DEFAULT true,
    gst_percent double precision DEFAULT 5 NOT NULL
);


ALTER TABLE public.room_types OWNER TO postgres;

--
-- Name: room_types_room_type_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.room_types_room_type_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.room_types_room_type_id_seq OWNER TO postgres;

--
-- Name: room_types_room_type_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.room_types_room_type_id_seq OWNED BY public.room_types.room_type_id;


--
-- Name: rooms; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.rooms (
    room_id integer NOT NULL,
    room_number character varying(10) NOT NULL,
    room_type_id integer,
    status character varying(20) NOT NULL
);


ALTER TABLE public.rooms OWNER TO postgres;

--
-- Name: rooms_room_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.rooms_room_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.rooms_room_id_seq OWNER TO postgres;

--
-- Name: rooms_room_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.rooms_room_id_seq OWNED BY public.rooms.room_id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    user_id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying NOT NULL,
    role character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: users_user_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.users_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_user_id_seq OWNER TO postgres;

--
-- Name: users_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.users_user_id_seq OWNED BY public.users.user_id;


--
-- Name: audit_logs log_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_logs ALTER COLUMN log_id SET DEFAULT nextval('public.audit_logs_log_id_seq'::regclass);


--
-- Name: bookings booking_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bookings ALTER COLUMN booking_id SET DEFAULT nextval('public.bookings_booking_id_seq'::regclass);


--
-- Name: contact_enquiries enquiry_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.contact_enquiries ALTER COLUMN enquiry_id SET DEFAULT nextval('public.contact_enquiries_enquiry_id_seq'::regclass);


--
-- Name: guests guest_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.guests ALTER COLUMN guest_id SET DEFAULT nextval('public.guests_guest_id_seq'::regclass);


--
-- Name: payments payment_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments ALTER COLUMN payment_id SET DEFAULT nextval('public.payments_payment_id_seq'::regclass);


--
-- Name: room_type_availability id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_type_availability ALTER COLUMN id SET DEFAULT nextval('public.room_type_availability_id_seq'::regclass);


--
-- Name: room_types room_type_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_types ALTER COLUMN room_type_id SET DEFAULT nextval('public.room_types_room_type_id_seq'::regclass);


--
-- Name: rooms room_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rooms ALTER COLUMN room_id SET DEFAULT nextval('public.rooms_room_id_seq'::regclass);


--
-- Name: users user_id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users ALTER COLUMN user_id SET DEFAULT nextval('public.users_user_id_seq'::regclass);


--
-- Data for Name: audit_logs; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.audit_logs (log_id, user_id, action, created_at) FROM stdin;
\.


--
-- Data for Name: bookings; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.bookings (booking_id, room_id, guest_id, check_in, check_out, booking_source, status, total_amount, created_at, base_amount, gst_amount, room_type_id, convenience_fee, convenience_gst, grand_total, cancelled_at, cancel_reason) FROM stdin;
1	\N	1	2026-02-01	2026-02-04	online	confirmed	4500.00	2026-01-21 22:09:46.196436	\N	\N	\N	0.00	0.00	\N	\N	\N
2	\N	2	2026-02-01	2026-02-04	online	confirmed	4725.00	2026-01-22 21:41:57.939431	\N	\N	\N	0.00	0.00	\N	\N	\N
4	\N	6	2026-01-10	2026-02-04	online	confirmed	39375.00	2026-01-24 21:58:43.665194	37500.00	1875.00	\N	0.00	0.00	\N	\N	\N
5	\N	7	2026-01-10	2026-02-04	online	confirmed	39375.00	2026-01-24 22:03:47.298036	37500.00	1875.00	\N	0.00	0.00	\N	\N	\N
6	\N	8	2025-01-11	2025-02-04	online	confirmed	37800.00	2026-01-24 22:04:08.118719	36000.00	1800.00	\N	0.00	0.00	\N	\N	\N
7	\N	9	2025-01-10	2025-02-04	online	cancelled	39375.00	2026-01-24 22:05:49.336733	37500.00	1875.00	\N	0.00	0.00	\N	\N	\N
8	\N	10	2025-01-30	2025-02-04	online	confirmed	7875.00	2026-01-26 14:30:34.005399	7500.00	375.00	1	0.00	0.00	\N	\N	\N
9	\N	11	2025-01-30	2025-02-04	online	confirmed	7875.00	2026-01-26 14:31:20.999961	7500.00	375.00	1	0.00	0.00	\N	\N	\N
11	\N	13	2025-01-30	2025-02-04	online	confirmed	7875.00	2026-01-26 14:44:27.037726	7500.00	375.00	1	0.00	0.00	\N	\N	\N
3	\N	5	2026-01-10	2026-02-04	online	cancelled	39375.00	2026-01-24 21:57:38.991802	37500.00	1875.00	\N	0.00	0.00	\N	\N	\N
12	\N	14	2025-01-30	2025-02-01	online	cancelled	3150.00	2026-01-26 20:57:12.615842	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
10	\N	12	2025-01-30	2025-02-04	online	cancelled	7875.00	2026-01-26 14:32:39.136505	7500.00	375.00	1	0.00	0.00	\N	\N	\N
18	\N	20	2026-02-21	2026-02-22	website	confirmed	2415.00	2026-02-21 18:28:09.507074	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
37	\N	39	2026-02-21	2026-02-23	website	confirmed	3150.00	2026-02-21 22:12:00.516066	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
38	\N	40	2026-02-21	2026-02-23	website	confirmed	3150.00	2026-02-21 22:14:04.636762	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
39	\N	41	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-21 22:15:58.285244	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
40	\N	42	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-22 18:46:00.354758	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
41	\N	43	2026-02-21	2026-02-23	website	payment_failed	4830.00	2026-02-22 18:58:03.042971	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
13	\N	15	2026-03-01	2026-03-03	website	payment_failed	3150.00	2026-02-20 21:18:20.039704	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
42	\N	44	2026-02-22	2026-02-23	website	confirmed	3850.00	2026-02-22 19:06:42.835755	3500.00	350.00	2	77.00	13.86	3940.86	\N	\N
53	\N	55	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-26 21:44:08.039215	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
50	\N	52	2026-02-21	2026-02-23	website	payment_failed	4830.00	2026-02-24 21:29:53.236996	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
54	\N	56	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-26 21:47:06.779462	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
49	\N	51	2026-02-21	2026-02-23	website	payment_failed	4830.00	2026-02-23 22:09:32.64991	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
52	\N	54	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-26 21:36:09.901666	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
55	\N	57	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-26 22:04:16.39211	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
88	\N	90	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-27 21:21:56.247976	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
89	\N	91	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-27 21:31:05.697043	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
90	\N	92	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-27 21:40:47.244729	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
91	\N	93	2026-02-21	2026-02-23	website	confirmed	3150.00	2026-02-27 21:53:00.590383	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
92	\N	94	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-27 21:58:48.308728	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
93	\N	95	2026-02-22	2026-02-23	website	confirmed	3850.00	2026-02-27 22:01:38.933391	3500.00	350.00	2	77.00	13.86	3940.86	\N	\N
94	\N	96	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-28 21:35:46.785861	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
95	\N	97	2026-02-21	2026-02-23	website	confirmed	4830.00	2026-02-28 21:38:43.046446	4600.00	230.00	3	96.60	17.39	4943.99	\N	\N
96	\N	98	2026-02-22	2026-02-23	website	confirmed	3850.00	2026-02-28 21:46:10.382535	3500.00	350.00	2	77.00	13.86	3940.86	\N	\N
14	\N	16	2026-02-21	2026-02-22	website	cancelled	2415.00	2026-02-21 18:26:19.786026	2300.00	115.00	3	48.30	8.69	2471.99	2026-03-16 21:41:41.380475	Payment timeout
15	\N	17	2026-02-21	2026-02-22	website	cancelled	2415.00	2026-02-21 18:26:33.545846	2300.00	115.00	3	48.30	8.69	2471.99	2026-03-16 21:41:41.380475	Payment timeout
16	\N	18	2026-02-21	2026-02-22	website	cancelled	2415.00	2026-02-21 18:27:01.069929	2300.00	115.00	3	48.30	8.69	2471.99	2026-03-16 21:41:41.380475	Payment timeout
17	\N	19	2026-02-21	2026-02-22	website	cancelled	2415.00	2026-02-21 18:27:15.268384	2300.00	115.00	3	48.30	8.69	2471.99	2026-03-16 21:41:41.380475	Payment timeout
19	\N	21	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:45:02.592023	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
20	\N	22	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:46:25.023346	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
21	\N	23	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:56:00.782692	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
22	\N	24	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:58:03.241304	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
23	\N	25	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:59:07.772998	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
24	\N	26	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 18:59:24.251081	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
25	\N	27	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 21:45:40.232814	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
26	\N	28	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 21:46:03.585155	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
27	\N	29	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 21:51:48.250857	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
28	\N	30	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 21:53:10.3468	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
29	\N	31	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:02:45.504216	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
30	\N	32	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:02:50.781072	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
31	\N	33	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:03:02.152156	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
32	\N	34	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:04:07.279517	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
33	\N	35	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:04:13.342537	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
34	\N	36	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:04:25.025964	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
35	\N	37	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:05:19.446537	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
36	\N	38	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-21 22:11:53.241378	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
43	\N	45	2026-02-21	2026-02-23	website	cancelled	4830.00	2026-02-23 21:46:21.719472	4600.00	230.00	3	96.60	17.39	4943.99	2026-03-16 21:41:41.380475	Payment timeout
44	\N	46	2026-02-21	2026-02-23	website	cancelled	4830.00	2026-02-23 21:46:32.40168	4600.00	230.00	3	96.60	17.39	4943.99	2026-03-16 21:41:41.380475	Payment timeout
45	\N	47	2026-02-21	2026-02-23	website	cancelled	4830.00	2026-02-23 21:47:09.331994	4600.00	230.00	3	96.60	17.39	4943.99	2026-03-16 21:41:41.380475	Payment timeout
46	\N	48	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-23 21:49:10.515418	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
47	\N	49	2026-02-21	2026-02-23	website	cancelled	3150.00	2026-02-23 21:49:36.681218	3000.00	150.00	1	63.00	11.34	3224.34	2026-03-16 21:41:41.380475	Payment timeout
48	\N	50	2026-02-21	2026-02-23	website	cancelled	4830.00	2026-02-23 22:06:04.98596	4600.00	230.00	3	96.60	17.39	4943.99	2026-03-16 21:41:41.380475	Payment timeout
51	\N	53	2026-02-22	2026-02-23	website	cancelled	3850.00	2026-02-24 21:32:05.805016	3500.00	350.00	2	77.00	13.86	3940.86	2026-03-16 21:41:41.380475	Payment timeout
97	\N	99	2026-02-21	2026-02-23	website	cancelled	4830.00	2026-02-28 22:15:21.913974	4600.00	230.00	3	96.60	17.39	4943.99	2026-03-16 21:41:41.380475	Payment timeout
98	\N	100	2026-03-18	2026-03-19	website	cancelled	1575.00	2026-03-16 21:25:26.04529	1500.00	75.00	1	31.50	5.67	1612.17	2026-03-16 21:41:41.380475	Payment timeout
99	\N	101	2026-03-18	2026-03-21	website	cancelled	4725.00	2026-03-16 21:25:57.93129	4500.00	225.00	1	94.50	17.01	4836.51	2026-03-16 21:41:41.380475	Payment timeout
106	\N	108	2026-03-20	2026-03-22	website	confirmed	3150.00	2026-03-19 21:58:20.233468	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
100	\N	102	2026-03-18	2026-03-26	website	confirmed	12600.00	2026-03-16 21:43:12.265315	12000.00	600.00	1	252.00	45.36	12897.36	\N	\N
101	\N	103	2026-03-18	2026-03-19	website	confirmed	1575.00	2026-03-16 21:55:08.410144	1500.00	75.00	1	31.50	5.67	1612.17	\N	\N
102	\N	104	2026-03-18	2026-03-19	website	confirmed	1575.00	2026-03-16 21:58:19.751512	1500.00	75.00	1	31.50	5.67	1612.17	\N	\N
103	\N	105	2026-02-22	2026-02-23	website	confirmed	3850.00	2026-03-16 21:59:21.388742	3500.00	350.00	2	77.00	13.86	3940.86	\N	\N
108	\N	110	2026-03-21	2026-03-22	website	payment_failed	2415.00	2026-03-20 21:20:21.066456	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
104	\N	106	2026-03-20	2026-03-22	website	confirmed	3150.00	2026-03-19 21:39:34.578345	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
105	\N	107	2026-03-20	2026-03-22	website	confirmed	3150.00	2026-03-19 21:45:17.504259	3000.00	150.00	1	63.00	11.34	3224.34	\N	\N
107	\N	109	2026-03-21	2026-03-22	website	cancelled	2415.00	2026-03-20 21:19:55.05993	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
109	\N	111	2026-03-21	2026-03-22	website	payment_failed	2415.00	2026-03-20 21:20:55.485999	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
110	\N	112	2026-03-21	2026-03-22	website	payment_failed	2415.00	2026-03-20 21:22:45.896498	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
111	\N	113	2026-03-23	2026-03-24	website	confirmed	2415.00	2026-03-20 21:24:23.269997	2300.00	115.00	3	48.30	8.69	2471.99	\N	\N
112	\N	114	2026-03-21	2026-03-30	website	payment_failed	21735.00	2026-03-20 21:30:07.478664	20700.00	1035.00	3	434.70	78.25	22247.95	\N	\N
113	\N	115	2026-03-22	2026-03-24	website	confirmed	7700.00	2026-03-20 21:31:12.265708	7000.00	700.00	2	154.00	27.72	7881.72	\N	\N
\.


--
-- Data for Name: contact_enquiries; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.contact_enquiries (enquiry_id, name, email, phone, message, status, created_at) FROM stdin;
1	abc	bhimasamogh@gmail.com	9876543210	Is room available on22nd march 2026	pending	2026-03-20 21:40:14.642347
\.


--
-- Data for Name: guests; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.guests (guest_id, name, phone, email, created_at) FROM stdin;
1	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-21 22:09:46.17342
2	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-22 21:41:57.922701
3	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 21:54:34.050576
4	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 21:54:38.603573
5	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 21:57:38.983232
6	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 21:58:43.614373
7	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 22:03:47.220922
8	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 22:04:08.046776
9	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-24 22:05:49.266476
10	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-26 14:30:33.984773
11	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-26 14:31:20.995743
12	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-26 14:32:39.111898
13	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-26 14:44:27.012863
14	Rahul Kumar	9876543210	rahul@gmail.com	2026-01-26 20:57:12.582056
15	Rahul Kumar	9876543210	rahul@example.com	2026-02-20 21:18:20.0126
16	xyz	9876543210	xyz@gmail.com	2026-02-21 18:26:19.73968
17	xyz	9876543210	xyz@gmail.com	2026-02-21 18:26:33.491148
18	xyz	9876543210	xyz@gmail.com	2026-02-21 18:27:01.060422
19	xyz	9876543210	xyz@gmail.com	2026-02-21 18:27:15.198347
20	xyz	9876543210	xyz@gmail.com	2026-02-21 18:28:09.496542
21	abc	9876543210	abc@gmail.com	2026-02-21 18:45:02.538106
22	abc	9876543210	abc@gmail.com	2026-02-21 18:46:24.951651
23	abc	9876543210	nbv@gmail.com	2026-02-21 18:56:00.759182
24	abc	198765441	nbv@gmail.com	2026-02-21 18:58:03.228119
25	abc	0987654321	nbv@gmail.com	2026-02-21 18:59:07.760626
26	abc	0987654321	nbv@gmail.com	2026-02-21 18:59:24.241367
27	abc	0987654321	nbv@gmail.com	2026-02-21 21:45:40.224798
28	abc	0987654321	nbv@gmail.com	2026-02-21 21:46:03.578882
29	abc	0987654321	nbv@gmail.com	2026-02-21 21:51:48.228698
30	abc	0987654321	nbv@gmail.com	2026-02-21 21:53:10.298081
31	abc	9876543210	xyz@gmail.com	2026-02-21 22:02:45.49572
32	abc	9876543210	xyz@gmail.com	2026-02-21 22:02:50.774079
33	abc	9876543210	xyz@gmail.com	2026-02-21 22:03:02.143311
34	abc	9876543210	xyz@gmail.com	2026-02-21 22:04:07.25681
35	abc	9876543210	xyz@gmail.com	2026-02-21 22:04:13.333444
36	abc	9876543210	xyz@gmail.com	2026-02-21 22:04:25.016028
37	abc	9876543210	xyz@gmail.com	2026-02-21 22:05:19.436239
38	abc	9876543210	xyz@gmail.com	2026-02-21 22:11:53.222418
39	abc	9876543210	xyz@gmail.com	2026-02-21 22:12:00.454531
40	abc	9876543210	xyz@gmail.com	2026-02-21 22:14:04.582143
41	akshay	9876543210	xyz@gmail.com	2026-02-21 22:15:58.276591
42	akshay	9876543210	xyz@gmail.com	2026-02-22 18:46:00.323954
43	akshay	9876543210	xyz@gmail.com	2026-02-22 18:58:03.020636
44	xyz	9876543210	xyz@gmail.com	2026-02-22 19:06:42.776421
45	akshay	9876543210	xyz@gmail.com	2026-02-23 21:46:21.671369
46	akshay	9876543210	xyz@gmail.com	2026-02-23 21:46:32.393472
47	akshay	9876543210	xyz@gmail.com	2026-02-23 21:47:09.321096
48	abc	9876543210	xyz@gmail.com	2026-02-23 21:49:10.504387
49	abc	9876543210	xyz@gmail.com	2026-02-23 21:49:36.666734
50	akshay	9876543210	xyz@gmail.com	2026-02-23 22:06:04.943682
51	akshay	9876543210	xyz@gmail.com	2026-02-23 22:09:32.639051
52	akshay	9876543210	xyz@gmail.com	2026-02-24 21:29:53.003888
53	xyz	9876543210	xyz@gmail.com	2026-02-24 21:32:05.798466
54	akshay	9876543210	xyz@gmail.com	2026-02-26 21:36:09.835974
55	akshay	9876543210	xyz@gmail.com	2026-02-26 21:44:08.016154
56	akshay	9876543210	xyz@gmail.com	2026-02-26 21:47:06.752358
57	akshay	9876543210	xyz@gmail.com	2026-02-26 22:04:16.355588
90	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 21:21:56.2036
91	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 21:31:05.676794
92	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 21:40:47.2201
93	abc	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 21:53:00.549806
94	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 21:58:48.279549
95	xyz	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-27 22:01:38.906669
96	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-28 21:35:46.728964
97	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-28 21:38:43.014896
98	xyz	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-28 21:46:10.350601
99	akshay	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-02-28 22:15:21.883426
100	abc	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-03-16 21:25:25.991444
101	abc	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-03-16 21:25:57.92268
102	abc	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-03-16 21:43:12.238568
103	abc	9876543210	bhimasamogh@gmail.com	2026-03-16 21:55:08.371154
104	abc	9876543210	bhimasamogh@gmail.com	2026-03-16 21:58:19.746208
105	xyz	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-03-16 21:59:21.378144
106	abc	9876543210	bhimasamogh@gmail.com	2026-03-19 21:39:34.54888
107	abc	9876543210	bhimasamogh@gmail.com	2026-03-19 21:45:17.495184
108	abc	9876543210	bhimasamogh@gmail.com	2026-03-19 21:58:20.218595
109	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:19:55.05993
110	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:20:21.066456
111	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:20:55.485999
112	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:22:45.896498
113	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:24:23.269997
114	abc	9876543210	bhimasamogh@gmail.com	2026-03-20 21:30:07.478664
115	xyz	9876543210	gopalakrishnan2110375@ssn.edu.in	2026-03-20 21:31:12.265708
\.


--
-- Data for Name: payments; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.payments (payment_id, booking_id, gateway, gateway_order_id, gateway_payment_id, amount, status, paid_at, order_id, payment_id_gateway, currency, created_at) FROM stdin;
1	13	razorpay	\N	\N	3150.00	created	\N	order_SISNofnD6FLeQD	\N	INR	2026-02-20 15:55:03.402982
2	14	razorpay	\N	\N	2415.00	created	\N	order_SInsBiY72TZVAn	\N	INR	2026-02-21 12:56:21.621248
3	15	razorpay	\N	\N	2415.00	created	\N	order_SInsOsa3Q6PRXR	\N	INR	2026-02-21 12:56:33.667676
4	16	razorpay	\N	\N	2415.00	created	\N	order_SInssvotyTn8Lx	\N	INR	2026-02-21 12:57:01.197483
5	17	razorpay	\N	\N	2415.00	created	\N	order_SInt8QLz1RFBzt	\N	INR	2026-02-21 12:57:15.390669
6	18	razorpay	\N	\N	2415.00	paid	\N	order_SInu5cfOProJtf	pay_SInv38fjwIQ1zt	INR	2026-02-21 12:58:09.661359
7	19	razorpay	\N	\N	3150.00	created	\N	order_SIoBx6NIrn4wML	\N	INR	2026-02-21 13:15:04.212761
8	20	razorpay	\N	\N	3150.00	created	\N	order_SIoDNRmeOkNEf5	\N	INR	2026-02-21 13:16:25.149728
9	21	razorpay	\N	\N	3150.00	created	\N	order_SIoNVwYqScVD6u	\N	INR	2026-02-21 13:26:00.945809
10	22	razorpay	\N	\N	3150.00	created	\N	order_SIoPfc6SEzasu9	\N	INR	2026-02-21 13:28:03.396144
11	23	razorpay	\N	\N	3150.00	created	\N	order_SIoQo2MTLauMgP	\N	INR	2026-02-21 13:29:07.910345
12	24	razorpay	\N	\N	3150.00	created	\N	order_SIoR5zqcECYVUR	\N	INR	2026-02-21 13:29:24.359113
13	25	razorpay	\N	\N	3150.00	created	\N	order_SIrH2Jss1QqUKg	\N	INR	2026-02-21 16:15:58.126933
14	26	razorpay	\N	\N	3150.00	created	\N	order_SIrH9zvWcGPZv7	\N	INR	2026-02-21 16:16:05.150523
15	27	razorpay	\N	\N	3224.34	created	\N	order_SIrNFeMYW0c0pV	\N	INR	2026-02-21 16:21:51.2333
16	28	razorpay	\N	\N	3224.34	created	\N	order_SIrOePYFsL4Ivz	\N	INR	2026-02-21 16:23:10.647098
17	30	razorpay	\N	\N	3224.34	created	\N	order_SIrYuI8c3hPxZ1	\N	INR	2026-02-21 16:32:53.300662
18	31	razorpay	\N	\N	3224.34	created	\N	order_SIrZ4GYd97nMH5	\N	INR	2026-02-21 16:33:02.413862
19	32	razorpay	\N	\N	3224.34	created	\N	order_SIraFQb3AlIrzN	\N	INR	2026-02-21 16:34:09.316113
20	33	razorpay	\N	\N	3224.34	created	\N	order_SIraKK6wi6cRqs	\N	INR	2026-02-21 16:34:13.796091
21	34	razorpay	\N	\N	3224.34	created	\N	order_SIraWmSWuAfbG3	\N	INR	2026-02-21 16:34:25.21482
22	35	razorpay	\N	\N	3224.34	created	\N	order_SIrbU9hRpt4PT7	\N	INR	2026-02-21 16:35:19.624583
23	37	razorpay	\N	\N	3224.34	paid	\N	order_SIriaDpCRNmULX	pay_SIriqZ45kI0IO1	INR	2026-02-21 16:42:02.832026
24	38	razorpay	\N	\N	3224.34	paid	\N	order_SIrkjS5NIu1Tdq	pay_SIrl8sZPpCpMTt	INR	2026-02-21 16:44:04.885793
25	39	razorpay	\N	\N	4943.99	paid	\N	order_SIrmjVDjZUMKRI	pay_SIrnBW5WNJkvyH	INR	2026-02-21 16:45:58.533697
26	40	razorpay	\N	\N	4943.99	paid	\N	order_SJCk50Zjwulcef	pay_SJCkMLKlrDDLnd	INR	2026-02-22 13:16:02.14306
27	41	razorpay	\N	\N	4943.99	created	\N	order_SJCwnpAaCWNlSi	\N	INR	2026-02-22 13:28:04.758326
28	42	razorpay	\N	\N	3940.86	created	\N	order_SJD5vUvrC5g5ho	\N	INR	2026-02-22 13:36:42.993977
29	43	razorpay	\N	\N	4943.99	created	\N	order_SJeLjFqA6st0nI	\N	INR	2026-02-23 16:16:24.379101
30	44	razorpay	\N	\N	4943.99	created	\N	order_SJeLs8pCrGJZ1b	\N	INR	2026-02-23 16:16:32.509093
31	45	razorpay	\N	\N	4943.99	created	\N	order_SJeMWSfatff7jN	\N	INR	2026-02-23 16:17:09.48544
32	46	razorpay	\N	\N	3224.34	created	\N	order_SJeOemJxCluwTN	\N	INR	2026-02-23 16:19:10.667405
33	47	razorpay	\N	\N	3224.34	created	\N	order_SJeP7MGg6S6IZL	\N	INR	2026-02-23 16:19:36.846204
34	42	razorpay	\N	\N	3940.86	created	\N	order_SJef8fC9xgyoCm	\N	INR	2026-02-23 16:34:46.841584
35	42	razorpay	\N	\N	3940.86	paid	\N	order_SJef8xDVkx5VFO	pay_SJefM1QSuXpdJM	INR	2026-02-23 16:34:47.095775
36	13	razorpay	\N	\N	3224.34	failed	\N	order_SJeg01SOLF7NOE	\N	INR	2026-02-23 16:35:35.72565
37	48	razorpay	\N	\N	4943.99	created	\N	order_SJegW72Y76PSPW	\N	INR	2026-02-23 16:36:05.108651
38	49	razorpay	\N	\N	4943.99	failed	\N	order_SJekAnc4ERA0P2	\N	INR	2026-02-23 16:39:32.773483
39	49	razorpay	\N	\N	4943.99	failed	\N	order_SJekKhHelBBZLy	\N	INR	2026-02-23 16:39:41.873528
40	49	razorpay	\N	\N	4943.99	created	\N	order_SJexfMH0cw3z7X	\N	INR	2026-02-23 16:52:19.14281
41	49	razorpay	\N	\N	4943.99	failed	\N	order_SJexgxUQypQnay	\N	INR	2026-02-23 16:52:20.632133
42	49	razorpay	\N	\N	4943.99	failed	\N	order_SJezhUkg6qMcTs	\N	INR	2026-02-23 16:54:14.749327
43	50	razorpay	\N	\N	4943.99	failed	\N	order_SK2bYTKq4iE03A	\N	INR	2026-02-24 16:00:01.44052
44	50	razorpay	\N	\N	4943.99	failed	\N	order_SK2dMCcDNKeUOH	\N	INR	2026-02-24 16:01:43.772068
45	51	razorpay	\N	\N	3940.86	failed	\N	order_SK2dkPEnX1oe5a	\N	INR	2026-02-24 16:02:05.942165
46	51	razorpay	\N	\N	3940.86	created	\N	order_SK2fD09hFqApLz	\N	INR	2026-02-24 16:03:28.938585
47	51	razorpay	\N	\N	3940.86	created	\N	order_SK2flMlLWqbuXw	\N	INR	2026-02-24 16:04:00.422609
48	51	razorpay	\N	\N	3940.86	created	\N	order_SK2gKqh4G8Mnlt	\N	INR	2026-02-24 16:04:32.929057
49	51	razorpay	\N	\N	3940.86	created	\N	order_SK2gyPBbMbWoIl	\N	INR	2026-02-24 16:05:09.187654
50	51	razorpay	\N	\N	3940.86	created	\N	order_SK2hEkn9xjzqTh	\N	INR	2026-02-24 16:05:24.147583
51	52	razorpay	\N	\N	4943.99	paid	\N	order_SKpmIripAobE8t	pay_SKpmX9CMfiFkAo	INR	2026-02-26 16:06:11.388032
52	53	razorpay	\N	\N	4943.99	paid	\N	order_SKpuic6MPVSGDl	pay_SKpusKyxiP18LH	INR	2026-02-26 16:14:09.375937
53	54	razorpay	\N	\N	4943.99	paid	\N	order_SKpxrnrErOPKdX	pay_SKpy2RS0wVtenZ	INR	2026-02-26 16:17:08.194856
54	55	razorpay	\N	\N	4943.99	paid	\N	order_SKqFznup4SiYP8	pay_SKqG9ujKX2QYQD	INR	2026-02-26 16:34:17.928458
87	88	razorpay	\N	\N	4943.99	paid	\N	order_SLE4NkhUOGACuj	pay_SLE4dLIO03d2DV	INR	2026-02-27 15:51:57.517813
88	89	razorpay	\N	\N	4943.99	paid	\N	order_SLEE3d6c61DD1M	pay_SLEEEZlOmUt4oX	INR	2026-02-27 16:01:07.07317
89	90	razorpay	\N	\N	4943.99	paid	\N	order_SLEOIcqpDgUUjc	pay_SLEOUsxrCMoyNc	INR	2026-02-27 16:10:48.817726
90	91	razorpay	\N	\N	3224.34	paid	\N	order_SLEbCIXyJI1ijE	pay_SLEbQw4BxgQgVH	INR	2026-02-27 16:23:01.449553
91	92	razorpay	\N	\N	4943.99	paid	\N	order_SLEhKJz9N8IaWl	pay_SLEhV4fqlL7oXu	INR	2026-02-27 16:28:49.579901
92	93	razorpay	\N	\N	3940.86	paid	\N	order_SLEkKftPDNh6Px	pay_SLEkaxdQmPRlnN	INR	2026-02-27 16:31:40.314566
93	94	razorpay	\N	\N	4943.99	paid	\N	order_SLcq8OAAXEGPJu	pay_SLcqOWbJ3WVO24	INR	2026-02-28 16:05:48.489485
94	95	razorpay	\N	\N	4943.99	paid	\N	order_SLctEbULpsTNEX	pay_SLcuP3RTrlKOhJ	INR	2026-02-28 16:08:44.563441
95	96	razorpay	\N	\N	3940.86	paid	\N	order_SLd16wFg8GeiIw	pay_SLd1LhowUHhQRr	INR	2026-02-28 16:16:11.94591
96	97	razorpay	\N	\N	4943.99	created	\N	order_SLdVw9RUZBasOL	\N	INR	2026-02-28 16:45:23.083996
97	98	razorpay	\N	\N	1612.17	created	\N	order_SRxD9vndKysUQ3	\N	INR	2026-03-16 15:55:28.013807
98	99	razorpay	\N	\N	4836.51	created	\N	order_SRxDgm9rPTH0tw	\N	INR	2026-03-16 15:55:58.099757
99	100	razorpay	\N	\N	12897.36	failed	\N	order_SRxVyEoxHZijFy	\N	INR	2026-03-16 16:13:16.578703
100	100	razorpay	\N	\N	12897.36	paid	\N	order_SRxWJrYGD113TE	pay_SRxWVZB7WYgWjN	INR	2026-03-16 16:13:36.397858
101	101	razorpay	\N	\N	1612.17	paid	\N	order_SRxiWhQobon6l4	pay_SRxiifKu1mpJkJ	INR	2026-03-16 16:25:09.930216
102	102	razorpay	\N	\N	1612.17	paid	\N	order_SRxls6Exel5QCx	pay_SRxm3e4pAFujGa	INR	2026-03-16 16:28:19.973133
103	103	razorpay	\N	\N	3940.86	paid	\N	order_SRxmyCSeRxtOnT	pay_SRxnAG9y2IzYCk	INR	2026-03-16 16:29:22.383263
104	104	razorpay	\N	\N	3224.34	failed	\N	order_ST93QlHefJU53O	\N	INR	2026-03-19 16:09:35.601082
105	104	razorpay	\N	\N	3224.34	failed	\N	order_ST945Hnfd9AplI	\N	INR	2026-03-19 16:10:12.716037
106	104	razorpay	\N	\N	3224.34	paid	\N	order_ST94CSRo0EFdmf	pay_ST94NP8AtjBHI2	INR	2026-03-19 16:10:19.284196
107	105	razorpay	\N	\N	3224.34	paid	\N	order_ST99SwycAFZsWd	pay_ST99euOx1VrEgy	INR	2026-03-19 16:15:18.39734
108	106	razorpay	\N	\N	3224.34	paid	\N	order_ST9NFMNuDPArNb	pay_ST9NP1DzbjxRQt	INR	2026-03-19 16:28:21.189504
109	107	razorpay	\N	\N	2471.99	failed	\N	order_STXFnSPfufBmuz	\N	INR	2026-03-20 15:49:56.231942
110	108	razorpay	\N	\N	2471.99	failed	\N	order_STXGEj6P5TRj8w	\N	INR	2026-03-20 15:50:21.205185
111	109	razorpay	\N	\N	2471.99	failed	\N	order_STXGqG3SZUML17	\N	INR	2026-03-20 15:50:55.58896
112	109	razorpay	\N	\N	2471.99	failed	\N	order_STXITdqZK2sRM5	\N	INR	2026-03-20 15:52:28.478021
113	110	razorpay	\N	\N	2471.99	failed	\N	order_STXImqXY6Hh0Do	\N	INR	2026-03-20 15:52:46.055277
114	111	razorpay	\N	\N	2471.99	paid	\N	order_STXKV8aY3eOXrk	pay_STXKifilGCHzre	INR	2026-03-20 15:54:23.439505
115	112	razorpay	\N	\N	22247.95	failed	\N	order_STXQa02mjsfRkq	\N	INR	2026-03-20 16:00:08.721382
116	113	razorpay	\N	\N	7881.72	paid	\N	order_STXRhWG2oanJTB	pay_STXRsQewGGCJIx	INR	2026-03-20 16:01:12.371643
\.


--
-- Data for Name: room_type_availability; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.room_type_availability (id, room_type_id, date, is_available, reason, created_at) FROM stdin;
3	1	2026-02-13	t		2026-02-12 21:05:24.787458
7	3	2026-03-12	t		2026-02-12 21:06:17.994773
9	1	2026-02-28	t		2026-02-28 22:10:25.273209
10	1	2026-03-17	t		2026-03-16 21:18:29.67995
\.


--
-- Data for Name: room_types; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.room_types (room_type_id, name, price_per_night, max_occupancy, created_at, is_active, gst_percent) FROM stdin;
2	Double Deluxe Ac	1900.00	2	2026-01-22 21:35:35.752056	t	5
3	Triple Bed Non/Ac	1550.00	3	2026-02-10 21:58:29.683188	t	5
4	Triple Bed Ac	2100.00	3	2026-03-21 21:59:33.969465	t	5
5	Four Bed Ac	2300.00	4	2026-03-21 22:02:04.147708	t	5
6	Four Bed Non/Ac	1750.00	4	2026-03-21 22:02:28.402063	t	5
7	Five Bed Ac	2700.00	5	2026-03-21 22:02:52.45152	t	5
8	Five Bed Non/Ac	1950.00	5	2026-03-21 22:03:06.072697	t	5
1	Double Deluxe Non/Ac	1150.00	2	2026-01-21 21:35:26.089824	t	5
\.


--
-- Data for Name: rooms; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.rooms (room_id, room_number, room_type_id, status) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: postgres
--

COPY public.users (user_id, username, password_hash, role, created_at) FROM stdin;
1	admin	$2b$12$NkBtmtZtoI6ieFmL28do0.XufkztQFWD.KrX0sdE/N/06z9od4tna	admin	2026-01-21 21:19:02.169385
2	user	$2b$12$28Dq/uJkm1DvZ3d7t7a7MuFD08NEceRnl/b3kQmiYFpLfwk5qaEaC	reception	2026-02-15 21:38:21.734014
\.


--
-- Name: audit_logs_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.audit_logs_log_id_seq', 1, false);


--
-- Name: bookings_booking_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.bookings_booking_id_seq', 113, true);


--
-- Name: contact_enquiries_enquiry_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.contact_enquiries_enquiry_id_seq', 1, true);


--
-- Name: guests_guest_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.guests_guest_id_seq', 115, true);


--
-- Name: payments_payment_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.payments_payment_id_seq', 116, true);


--
-- Name: room_type_availability_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.room_type_availability_id_seq', 13, true);


--
-- Name: room_types_room_type_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.room_types_room_type_id_seq', 8, true);


--
-- Name: rooms_room_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.rooms_room_id_seq', 1, false);


--
-- Name: users_user_id_seq; Type: SEQUENCE SET; Schema: public; Owner: postgres
--

SELECT pg_catalog.setval('public.users_user_id_seq', 2, true);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (log_id);


--
-- Name: bookings bookings_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_pkey PRIMARY KEY (booking_id);


--
-- Name: contact_enquiries contact_enquiries_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.contact_enquiries
    ADD CONSTRAINT contact_enquiries_pkey PRIMARY KEY (enquiry_id);


--
-- Name: guests guests_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.guests
    ADD CONSTRAINT guests_pkey PRIMARY KEY (guest_id);


--
-- Name: payments payments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_pkey PRIMARY KEY (payment_id);


--
-- Name: room_type_availability room_type_availability_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_type_availability
    ADD CONSTRAINT room_type_availability_pkey PRIMARY KEY (id);


--
-- Name: room_types room_types_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_types
    ADD CONSTRAINT room_types_pkey PRIMARY KEY (room_type_id);


--
-- Name: rooms rooms_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rooms
    ADD CONSTRAINT rooms_pkey PRIMARY KEY (room_id);


--
-- Name: rooms rooms_room_number_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rooms
    ADD CONSTRAINT rooms_room_number_key UNIQUE (room_number);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (user_id);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: ix_audit_logs_log_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_audit_logs_log_id ON public.audit_logs USING btree (log_id);


--
-- Name: ix_bookings_booking_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_bookings_booking_id ON public.bookings USING btree (booking_id);


--
-- Name: ix_contact_enquiries_created_at; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_created_at ON public.contact_enquiries USING btree (created_at);


--
-- Name: ix_contact_enquiries_email; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_email ON public.contact_enquiries USING btree (email);


--
-- Name: ix_contact_enquiries_enquiry_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_enquiry_id ON public.contact_enquiries USING btree (enquiry_id);


--
-- Name: ix_contact_enquiries_name; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_name ON public.contact_enquiries USING btree (name);


--
-- Name: ix_contact_enquiries_phone; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_phone ON public.contact_enquiries USING btree (phone);


--
-- Name: ix_contact_enquiries_status; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_contact_enquiries_status ON public.contact_enquiries USING btree (status);


--
-- Name: ix_guests_guest_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_guests_guest_id ON public.guests USING btree (guest_id);


--
-- Name: ix_payments_payment_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_payments_payment_id ON public.payments USING btree (payment_id);


--
-- Name: ix_room_type_availability_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_room_type_availability_id ON public.room_type_availability USING btree (id);


--
-- Name: ix_room_types_room_type_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_room_types_room_type_id ON public.room_types USING btree (room_type_id);


--
-- Name: ix_rooms_room_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_rooms_room_id ON public.rooms USING btree (room_id);


--
-- Name: ix_users_user_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX ix_users_user_id ON public.users USING btree (user_id);


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(user_id);


--
-- Name: bookings bookings_guest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_guest_id_fkey FOREIGN KEY (guest_id) REFERENCES public.guests(guest_id);


--
-- Name: bookings bookings_room_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT bookings_room_id_fkey FOREIGN KEY (room_id) REFERENCES public.rooms(room_id);


--
-- Name: bookings fk_bookings_room_type; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.bookings
    ADD CONSTRAINT fk_bookings_room_type FOREIGN KEY (room_type_id) REFERENCES public.room_types(room_type_id);


--
-- Name: payments payments_booking_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.payments
    ADD CONSTRAINT payments_booking_id_fkey FOREIGN KEY (booking_id) REFERENCES public.bookings(booking_id);


--
-- Name: room_type_availability room_type_availability_room_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.room_type_availability
    ADD CONSTRAINT room_type_availability_room_type_id_fkey FOREIGN KEY (room_type_id) REFERENCES public.room_types(room_type_id);


--
-- Name: rooms rooms_room_type_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.rooms
    ADD CONSTRAINT rooms_room_type_id_fkey FOREIGN KEY (room_type_id) REFERENCES public.room_types(room_type_id);


--
-- PostgreSQL database dump complete
--

